import argparse
import json
import logging
import threading
import time
from pathlib import Path

from .core.config import Settings
from .core.domain import Asset, Candidate, Plan, Product, Transcript, new_id
from .media.editing import build_plan, validate_plan
from .media.exporting import export_plan
from .media.processing import convert_to_mp4, import_asset
from .core.storage import Store

logger = logging.getLogger("douyincut.worker")


class Paused(Exception):
    pass


class Cancelled(Exception):
    pass


class Context:
    def __init__(self, store: Store, job: dict, owner: str):
        self.store, self.job, self.owner = store, job, owner
        self.lost = False

    def checkpoint(self):
        if self.lost:
            raise Cancelled("任务租约失效")
        state = self.store.job(self.job["id"])["state"]
        if state == "pause_requested":
            raise Paused()
        if state == "cancel_requested":
            raise Cancelled()

    def progress(self, value):
        self.store.progress(self.job["id"], self.owner, value)


class Worker:
    def __init__(self, settings: Settings, *, interruptible_cloud: bool = False):
        self.settings = settings
        self.interruptible_cloud = interruptible_cloud
        settings.prepare()
        self.store = Store(settings.database)
        self.owner = new_id("worker")

    def execute(self, job, ctx):
        store, settings = self.store, self.settings
        key = f"job-result:{job['id']}"
        cached = store.cached(key)
        if cached is not None:
            if job["kind"] == "import" and job["payload"].get("live_product_clip_id") and job["payload"].get("product_name"):
                from .projects.service import new_project_draft, save_draft
                save_draft(store, cached["asset_id"], new_project_draft(store, product_name=job["payload"]["product_name"]))
            if job["kind"] == "import" and job["payload"].get("live_segment_id"):
                from .live_monitoring import LiveStore
                LiveStore(store).finish_import(job["payload"]["live_segment_id"], cached["asset_id"])
            if job["kind"] == "import" and job["payload"].get("live_product_clip_id"):
                from .live_monitoring import LiveStore
                LiveStore(store).finish_product_clip_import(job["payload"]["live_product_clip_id"], cached["asset_id"])
            return cached
        payload = job["payload"]
        objects, followup = [], None
        if job["kind"] == "import":
            source_path = Path(payload["path"])
            display_name = payload.get("display_name")
            if (source_path.suffix.lower() == ".ts"
                    and (payload.get("live_segment_id") or payload.get("live_product_clip_id"))):
                converted_path = settings.data_dir / "recordings" / "imports" / f"{job['id']}.mp4"
                convert_to_mp4(source_path, converted_path, settings, ctx.checkpoint)
                source_path = converted_path
                if display_name:
                    display_name = f"{Path(display_name).stem}.mp4"
            asset = import_asset(source_path, settings, ctx.checkpoint)
            if display_name:
                asset.name = display_name
            objects.append(("asset", asset.model_dump()))
            result = {"asset_id": asset.id}
        elif job["kind"] in {"export", "preview"}:
            plan = Plan.model_validate(store.get("plan", payload["plan_id"], payload["revision"]))
            if job["kind"] == "export" and plan.status != "approved":
                raise ValueError("最终导出需要先审核确认方案")
            assets = {clip.source_asset_id: Asset.model_validate(store.get("asset", clip.source_asset_id)) for clip in plan.clips}
            candidates = {clip.candidate_id: Candidate.model_validate(store.get("candidate", clip.candidate_id)) for clip in plan.clips}
            product = Product.model_validate(store.get("product", plan.product_id))
            validate_plan(plan, product, candidates, assets)
            result = export_plan(plan, assets, settings, job["id"], package=payload["format"] == "package",
                                 checkpoint=ctx.checkpoint, progress=ctx.progress, preview=job["kind"] == "preview")
            objects.append(("export", result))
        elif job["kind"] == "live_product_clips":
            from .live_monitoring.service import generate_period_product_clips
            result = generate_period_product_clips(store, settings, payload,
                                                    checkpoint=ctx.checkpoint, progress=ctx.progress)
        elif job["kind"] == "jianying":
            from .integrations.jianying.service import create_jianying_draft
            plan = Plan.model_validate(store.get("plan", payload["plan_id"], payload["revision"]))
            assets = {clip.source_asset_id: Asset.model_validate(store.get("asset", clip.source_asset_id))
                      for clip in plan.clips}
            candidates = {clip.candidate_id: Candidate.model_validate(store.get("candidate", clip.candidate_id))
                          for clip in plan.clips}
            product = Product.model_validate(store.get("product", plan.product_id))
            validate_plan(plan, product, candidates, assets)
            result = create_jianying_draft(plan, assets, settings, job["id"],
                                           checkpoint=ctx.checkpoint, progress=ctx.progress)
        elif job["kind"] == "analyze":
            from .ai.providers import CloudConfig, transcribe
            from .ai.roughcut import (RoughcutRequest, TimeSelection, candidates_from_intervals,
                                   select_intervals, validate_selection_duration, validate_transcripts)
            asset = Asset.model_validate(store.get("asset", payload["asset_id"]))
            config = CloudConfig.model_validate(payload["config"])
            workdir = settings.data_dir / "analysis" / job["id"]
            workdir.mkdir(parents=True, exist_ok=True)
            request = RoughcutRequest.model_validate({field: payload[field] for field in RoughcutRequest.model_fields
                                                     if field in payload})
            transcripts = validate_transcripts(transcribe(asset, config, settings, store, workdir, ctx.checkpoint,
                                                          lambda p: ctx.progress(p * 0.7), require_sentence_timing=True,
                                                          refresh_scope=job["id"] if request.refresh_asr else None,
                                                          job_id=job["id"] if self.interruptible_cloud else None), asset)
            # Publish the complete source dialogue before calling the planner, even if it later fails.
            store.cache(f"roughcut-transcript:{job['id']}", {"job_id": job["id"], "source_asset_id": asset.id,
                        "duration_us": asset.duration_us, "ready": True,
                        "sentences": [t.model_dump() for t in transcripts]})
            ctx.progress(0.7)
            ctx.checkpoint()
            selection_key = f"roughcut-timestamps-v1:{job['id']}"
            selected = store.cached(selection_key)
            if selected is None:
                operation = (store, job["id"], ctx.checkpoint) if self.interruptible_cloud else None
                selected = select_intervals(transcripts, request, config, asset.duration_us,
                                            operation=operation).model_dump()
                store.cache(selection_key, selected)
            ctx.checkpoint()
            product = Product(name=request.product_name, confirmed=True)
            decision = TimeSelection.model_validate(selected)
            validate_selection_duration(decision, request.target_duration_us, request.duration_tolerance_us)
            candidates = candidates_from_intervals(decision, transcripts, product.id)
            plan = build_plan(product, candidates, {asset.id: asset}, transcripts, request.target_duration_us,
                              request.instructions, ordered_ids=[c.id for c in candidates],
                              tolerance_us=request.duration_tolerance_us)
            result = {"asset_id": asset.id, "product_id": product.id, "plan_id": plan.id,
                      "candidate_ids": [c.id for c in candidates], "transcript_count": len(transcripts),
                      "selected_intervals": decision.model_dump()["clips"], "timestamp_unit": "milliseconds",
                      "mode": "asr_text_roughcut"}
            rows = [("product", product.model_dump()), ("plan", plan.model_dump())]
            rows += [("candidate", c.model_dump()) for c in candidates]
            rows += [("transcript", {"id": new_id("transcript"), "segment": t.model_dump()}) for t in transcripts]
            store.commit_result(key, result, rows,
                                ("preview", {"plan_id": plan.id, "revision": plan.revision, "format": "mp4"}),
                                job_id=job["id"], owner=self.owner, replace_transcripts_for=asset.id)
            return result
        else:
            raise ValueError("未知任务类型")
        store.commit_result(key, result, objects, followup, job_id=job["id"], owner=self.owner)
        if job["kind"] == "import" and payload.get("live_product_clip_id") and payload.get("product_name"):
            from .projects.service import new_project_draft, save_draft
            save_draft(store, result["asset_id"], new_project_draft(store, product_name=payload["product_name"]))
        if job["kind"] == "import" and payload.get("live_segment_id"):
            from .live_monitoring import LiveStore
            LiveStore(store).finish_import(payload["live_segment_id"], result["asset_id"])
        if job["kind"] == "import" and payload.get("live_product_clip_id"):
            from .live_monitoring import LiveStore
            LiveStore(store).finish_product_clip_import(payload["live_product_clip_id"], result["asset_id"])
        return result

    def run_once(self):
        job = self.store.claim(self.owner)
        if job is None:
            return False
        ctx = Context(self.store, job, self.owner)
        stop = threading.Event()

        def heartbeat():
            while not stop.wait(10):
                if not self.store.heartbeat(job["id"], self.owner):
                    ctx.lost = True
                    return

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            ctx.checkpoint()
            result = self.execute(job, ctx)
            self.store.finish(job["id"], self.owner, "succeeded", result=result)
            if job["kind"] == "live_product_clips":
                self._notify_live_product_split(job["payload"])
        except Paused:
            self.store.finish(job["id"], self.owner, "paused")
        except Cancelled:
            if not ctx.lost:
                self.store.finish(job["id"], self.owner, "cancelled")
                if job["kind"] == "import" and job["payload"].get("live_segment_id"):
                    from .live_monitoring import LiveStore
                    LiveStore(self.store).retry_import(job["payload"]["live_segment_id"])
                if job["kind"] == "import" and job["payload"].get("live_product_clip_id"):
                    from .live_monitoring import LiveStore
                    LiveStore(self.store).retry_product_clip_import(job["payload"]["live_product_clip_id"])
        except Exception as exc:
            from .ai.providers import ProviderError
            # A cloud reply that already failed is a real failure, even if exit was confirmed meanwhile.
            if (not isinstance(exc, ProviderError) and self.store.is_shutting_down()
                    and self.store.job(job["id"])["state"] == "pause_requested"):
                self.store.finish(job["id"], self.owner, "paused")
                return True
            # Expected errors are sanitized by providers and media handlers.
            message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else f"任务失败（{type(exc).__name__}）"
            if isinstance(exc, OSError) and job['kind'] in {'export', 'preview'}:
                output_root = self.settings.data_dir / ('previews' if job['kind'] == 'preview' else 'exports')
                message = f'媒体输出写入失败：{output_root}；请检查权限、磁盘空间或文件占用（{type(exc).__name__}）'
            self.store.finish(job["id"], self.owner, "failed", error=message[:1000],
                              error_detail=getattr(exc, "details", None))
            if job["kind"] == "import" and job["payload"].get("live_segment_id"):
                from .live_monitoring import LiveStore
                LiveStore(self.store).retry_import(job["payload"]["live_segment_id"])
            if job["kind"] == "import" and job["payload"].get("live_product_clip_id"):
                from .live_monitoring import LiveStore
                LiveStore(self.store).retry_product_clip_import(job["payload"]["live_product_clip_id"])
            logger.error("job %s failed: %s", job["id"], type(exc).__name__,
                         exc_info=isinstance(exc, OSError) and job['kind'] in {'export', 'preview'})
        finally:
            stop.set()
            thread.join(timeout=2)
        return True

    def _notify_live_product_split(self, payload):
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT m.name,c.notify_split_complete FROM live_product_periods p "
                "JOIN live_sessions s ON s.id=p.session_id "
                "JOIN live_monitors m ON m.id=s.monitor_id "
                "JOIN live_monitor_controls c ON c.monitor_id=m.id WHERE p.id=?",
                (payload["period_id"],),
            ).fetchone()
        if row is None or not row["notify_split_complete"]:
            return
        from .live_monitoring.notifications import show_toast
        show_toast(f"{row['name'] or '抖音直播间'} 商品切分完成", "商品视频片段已生成，可在直播监控中查看")

    def run(self, stop=None):
        stop = stop if stop is not None else threading.Event()
        while not stop.is_set():
            if not self.run_once():
                stop.wait(0.5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    from .core.logs import configure_logging
    configure_logging("worker")
    worker = Worker(Settings.from_env())
    worker.run_once() if args.once else worker.run()


if __name__ == "__main__":
    main()
