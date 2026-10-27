import asyncio
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import Field

from .core.config import Settings
from .core.credentials import read_secret, secret_preview, write_secret
from .core.domain import Asset, Candidate, Model, Plan, Product, Transcript
from .media.editing import make_batches, remap_subtitles, validate_plan
from .media.processing import diagnostics, probe_stream_timing
from .ai.providers import CloudConfig, ProviderError, asr_api_key, model_api_key
from .ai.connection import ModelConnectionTestRequest, check_model_connection
from .ai.roughcut import DEFAULT_INSTRUCTIONS, RoughcutRequest
from .core.storage import Store
from .projects.service import (EditingDefaults, editing_defaults, save_editing_defaults, ProjectDraft, delete_plan, delete_project, job_summary,
                       project_detail, project_list, save_draft)
from .live_monitoring import (LiveClipOpen, LiveMonitorRequest, LiveMonitorSettings, LiveRecordingAction, LiveStore)


class ExportRequest(Model):
    format: Literal["mp4"] = "mp4"


class JianyingDraftRequest(Model):
    revision: int = Field(ge=1)


class SecretRequest(Model):
    name: Literal["llm_api_key", "asr_api_key", "oss_access_key_id", "oss_access_key_secret", "douyin_cookie"]
    value: str = Field(max_length=20000)


def create_app(settings: Settings | None = None, token: str | None = None, *, frontend_dir: Path | None = None):
    settings = settings or Settings.from_env()
    settings.prepare()
    store = Store(settings.database)
    live_store = LiveStore(store)
    token = token or os.environ.get("DOUYINCUT_SESSION_TOKEN") or secrets.token_urlsafe(32)
    app = FastAPI(title="DouyinCut", version="0.1.0")
    app.state.store, app.state.settings, app.state.live_store = store, settings, live_store
    allowed = {f"http://127.0.0.1:{settings.port}"}
    if frontend_dir is None:
        allowed.update({"http://127.0.0.1:5173", "http://tauri.localhost", "https://tauri.localhost"})
    else:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1'])
    app.add_middleware(CORSMiddleware, allow_origins=list(allowed), allow_credentials=True,
                       allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Content-Type", "X-Session-Token"])

    @app.middleware("http")
    async def local_auth(request: Request, call_next):
        trace = secrets.token_hex(8)
        if request.url.path.startswith("/api/") and request.method != "OPTIONS":
            origin = request.headers.get("origin")
            provided = request.headers.get("x-session-token") or request.cookies.get("douyincut_session", "")
            if (origin is not None and origin not in allowed) or not hmac.compare_digest(provided.encode(), token.encode()):
                return JSONResponse({"detail": "本地会话未授权，请通过项目启动器打开界面"}, status_code=401)
        response = await call_next(request)
        response.headers["X-Request-ID"] = trace
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        if frontend_dir is not None:
            from .desktop_frontend import PRODUCTION_CSP
            response.headers['Content-Security-Policy'] = PRODUCTION_CSP
            response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "找不到对应记录"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(ProviderError)
    async def provider_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def bad_input(request, exc):
        # Validation errors must not echo submitted secrets in their `input` fields.
        return JSONResponse({"detail": "请求参数无效，请检查字段类型、范围和必填项"}, status_code=422)

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.post("/api/v1/auth")
    def authenticate(response: Response):
        response.set_cookie("douyincut_session", token, httponly=True, samesite="strict", path="/api/v1")
        return {"authenticated": True}

    @app.get("/api/v1/diagnostics")
    def diagnostic():
        return {**diagnostics(settings), "data_dir": str(settings.data_dir),
                "jianying": {"version": "11.6.0", "status": "untested"},
                "douyin_resolver": "streamget", "cloud_integration": "awaiting_live_validation"}

    @app.get("/api/v1/live/monitors")
    def live_monitors(window_start: float | None = Query(None, ge=0, allow_inf_nan=False),
                      window_end: float | None = Query(None, gt=0, allow_inf_nan=False),
                      include_end_recording: bool = False):
        if (window_start is None) != (window_end is None):
            raise HTTPException(422, "录制时间窗口必须同时提供开始和结束")
        try:
            return live_store.list_monitors(window_start=window_start, window_end=window_end,
                                            include_end_recording=include_end_recording)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/v1/live/monitors/{monitor_id}/sessions/{session_id}")
    def live_recording_session(monitor_id: str, session_id: str):
        return live_store.recording_session(monitor_id, session_id)

    @app.post("/api/v1/live/monitors", status_code=201)
    def create_live_monitor(body: LiveMonitorRequest):
        return live_store.create_monitor(body.url, body.name)

    @app.patch("/api/v1/live/monitors/{monitor_id}")
    def change_live_monitor(monitor_id: str, body: LiveMonitorSettings):
        return live_store.update_settings(monitor_id, body)

    @app.post("/api/v1/live/monitors/{monitor_id}/recording")
    def control_live_recording(monitor_id: str, body: LiveRecordingAction):
        try:
            return live_store.request_recording(monitor_id, body.action)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.delete("/api/v1/live/monitors/{monitor_id}")
    def remove_live_monitor(monitor_id: str):
        try:
            return live_store.delete_monitor(monitor_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/v1/live/segments/{segment_id}/import", status_code=202)
    def import_live_segment(segment_id: str):
        try:
            return live_store.import_segment(segment_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/v1/live/product-clips/{clip_id}/import", status_code=202)
    def import_live_product_clip(clip_id: str):
        try:
            return live_store.import_product_clip(clip_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/v1/live/product-clips/{clip_id}/open")
    def open_live_product_clip(clip_id: str, body: LiveClipOpen):
        try:
            return live_store.open_product_clip(
                clip_id, settings.data_dir / "recordings" / "live", reveal=body.reveal)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.delete("/api/v1/live/product-clips/{clip_id}")
    def delete_live_product_clip(clip_id: str):
        try:
            return live_store.delete_product_clip(
                clip_id, settings.data_dir / "recordings" / "live")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/v1/live/segments/{segment_id}/product-clips", status_code=202)
    def split_live_segment(segment_id: str):
        try:
            return live_store.request_segment_product_clips(segment_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.delete("/api/v1/live/segments/{segment_id}")
    def delete_live_segment(segment_id: str):
        try:
            return live_store.delete_segment(
                segment_id, settings.data_dir / "recordings" / "live"
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/v1/assets/upload", status_code=202)
    async def upload_file(request: Request, filename: str = Query(min_length=1, max_length=255)):
        display_name = filename.replace("\\", "/").rsplit("/", 1)[-1]
        suffix = Path(display_name).suffix.lower()
        if suffix not in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".ts", ".m4v"}:
            raise ValueError("请选择支持的视频文件")
        folder = settings.data_dir / "recordings" / "imports"
        final = folder / (secrets.token_hex(16) + suffix)
        temporary = final.with_suffix(suffix + ".partial")
        size = 0
        try:
            folder.mkdir(parents=True, exist_ok=True)
            with temporary.open("wb") as output:
                async for chunk in request.stream():
                    if chunk:
                        await asyncio.to_thread(output.write, chunk)
                        size += len(chunk)
            if not size:
                raise ValueError("所选视频为空文件")
            temporary.rename(final)
            return store.enqueue("import", {"path": str(final), "display_name": display_name})
        except BaseException as exc:
            for owned in (temporary, final):
                try:
                    owned.unlink(missing_ok=True)
                except OSError:
                    pass  # Cleanup failure must not hide the original write error.
            if isinstance(exc, OSError):
                raise HTTPException(503, f'视频导入写入失败：{folder}；请检查权限、磁盘空间或文件占用') from None
            raise

    @app.get("/api/v1/assets")
    def assets(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return store.list("asset", limit, offset)

    @app.get("/api/v1/projects")
    def projects(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                 search: str = Query("", max_length=200),
                 status: Literal['all', 'pending', 'processing', 'paused', 'failed', 'completed', 'cancelled'] = 'all'):
        return project_list(store, page, page_size, search, status)

    @app.get("/api/v1/projects/{asset_id}")
    def get_project(asset_id: str):
        detail = project_detail(store, asset_id)
        source_path = Path(detail["asset"]["path"])
        if source_path.is_file():
            detail["asset"].update(probe_stream_timing(source_path, settings))
        return detail

    @app.patch("/api/v1/projects/{asset_id}")
    def update_project(asset_id: str, body: ProjectDraft):
        return save_draft(store, asset_id, body)

    @app.delete("/api/v1/projects/{asset_id}")
    def remove_project(asset_id: str):
        try:
            return delete_project(store, asset_id, settings.data_dir)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get("/api/v1/jobs-summary")
    def summary():
        return job_summary(store)

    @app.get("/api/v1/assets/{asset_id}/media")
    def asset_media(asset_id: str):
        asset = Asset.model_validate(store.get("asset", asset_id))
        source = Path(asset.path)
        if not source.is_file():
            raise HTTPException(410, "源素材已移动或删除")
        return FileResponse(asset.path)

    @app.get("/api/v1/assets/{asset_id}/batches")
    def asset_batches(asset_id: str):
        asset = Asset.model_validate(store.get("asset", asset_id))
        return [b.model_dump() for b in make_batches([asset], flush=True)]

    @app.post("/api/v1/assets/{asset_id}/analyze", status_code=202)
    def analyze(asset_id: str, body: RoughcutRequest):
        asset = Asset.model_validate(store.get("asset", asset_id))
        if not body.product_name.strip():
            raise ValueError("请先填写商品名称")
        if not asset.has_audio:
            raise ValueError("素材没有音轨，无法进行 ASR 粗剪")
        config = cloud_config()
        model_api_key(config)
        asr_api_key(config)
        if not config.oss_bucket or not config.oss_endpoint:
            raise ValueError("Qwen 语音识别需要 OSS 上传配置")
        with store.connection() as conn:
            active = conn.execute("SELECT 1 FROM jobs WHERE kind='analyze' AND json_extract(payload,'$.asset_id')=? "
                                  "AND state IN ('queued','running','pause_requested','paused','cancel_requested') LIMIT 1", (asset_id,)).fetchone()
        if active:
            raise HTTPException(409, "该素材已有粗剪任务，请先完成或取消原任务")
        save_draft(store, asset_id, ProjectDraft(**{field: getattr(body, field) for field in ProjectDraft.model_fields}))
        return store.enqueue("analyze", {"asset_id": asset_id, "config": config.model_dump(),
                             **body.model_dump(), "product_name": body.product_name.strip(),
                             "instructions": body.instructions.strip() or DEFAULT_INSTRUCTIONS})

    @app.get("/api/v1/jobs")
    def jobs(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return store.jobs(limit, offset)

    @app.get("/api/v1/jobs/{job_id}")
    def job(job_id: str):
        return store.job(job_id)

    @app.get("/api/v1/jobs/{job_id}/transcript")
    def job_transcript(job_id: str):
        task = store.job(job_id)
        if task["kind"] != "analyze":
            raise HTTPException(422, "该任务没有 ASR 转写")
        saved = store.cached(f"roughcut-transcript:{job_id}")
        if saved:
            return saved
        return {"job_id": job_id, "source_asset_id": task["payload"].get("asset_id"), "ready": False, "sentences": []}

    @app.get("/api/v1/jobs/{job_id}/error-detail")
    def job_error_detail(job_id: str):
        return store.job_error_detail(job_id)

    @app.post("/api/v1/jobs/{job_id}/{action}")
    def control(job_id: str, action: Literal["pause", "resume", "cancel", "retry", "acknowledge_asr_uncertain"]):
        config = cloud_config().model_dump() if action in {"retry", "acknowledge_asr_uncertain"} and store.job(job_id)["kind"] == "analyze" else None
        return store.control(job_id, action, retry_config=config)

    @app.get("/api/v1/products")
    def products(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return store.list("product", limit, offset)

    @app.get("/api/v1/candidates")
    def candidates(product_id: str | None = None, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        data = store.list("candidate", limit=10000)
        if product_id:
            data = [c for c in data if c["product_id"] == product_id]
        return data[offset:offset + limit]

    @app.get("/api/v1/plans")
    def plans(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return store.list("plan", limit, offset)

    @app.delete("/api/v1/plans/{plan_id}")
    def remove_plan(plan_id: str):
        try:
            return delete_plan(store, plan_id, settings.data_dir)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    def plan_dependencies(plan: Plan):
        product = Product.model_validate(store.get("product", plan.product_id))
        assets = {c.source_asset_id: Asset.model_validate(store.get("asset", c.source_asset_id)) for c in plan.clips}
        candidates = {c.candidate_id: Candidate.model_validate(store.get("candidate", c.candidate_id)) for c in plan.clips}
        validate_plan(plan, product, candidates, assets)

    @app.patch("/api/v1/plans/{plan_id}")
    def change_plan(plan_id: str, body: Plan):
        if body.id != plan_id:
            raise ValueError("方案 ID 不一致")
        previous = store.get("plan", plan_id)
        if previous["revision"] != body.revision:
            raise HTTPException(409, "方案已被其他操作更新，请刷新后重试")
        plan_dependencies(body)
        asset_ids = list({clip.source_asset_id for clip in body.clips})
        with store.connection() as conn:
            rows = conn.execute("SELECT body FROM objects WHERE kind='transcript' AND "
                                "json_extract(body,'$.segment.source_asset_id') IN (" +
                                ",".join("?" for _ in asset_ids) + ")", asset_ids).fetchall()
        transcripts = [Transcript.model_validate(json.loads(t[0])["segment"]) for t in rows]
        clips_changed = previous["clips"] != [c.model_dump() for c in body.clips]
        subtitles = remap_subtitles(body.clips, transcripts) if clips_changed else body.subtitles
        body = Plan.model_validate({**body.model_dump(), "status": "needs_review",
                                    "subtitles": [s.model_dump() for s in subtitles]})
        result = store.next_revision("plan", plan_id, body.model_dump(), expected_revision=body.revision)
        store.enqueue("preview", {"plan_id": plan_id, "revision": result["revision"], "format": "mp4"})
        return result

    @app.post("/api/v1/plans/{plan_id}/approve")
    def approve_plan(plan_id: str):
        plan = Plan.model_validate(store.get("plan", plan_id))
        plan_dependencies(plan)
        return store.next_revision("plan", plan_id, plan.model_copy(update={"status": "approved"}).model_dump(), expected_revision=plan.revision)

    @app.post("/api/v1/plans/{plan_id}/exports", status_code=202)
    def export(plan_id: str, body: ExportRequest):
        plan = Plan.model_validate(store.get("plan", plan_id))
        plan_dependencies(plan)
        if plan.status != "approved":
            raise ValueError("请先审核确认方案")
        return store.enqueue("export", {"plan_id": plan.id, "revision": plan.revision, "format": body.format})

    @app.post("/api/v1/plans/{plan_id}/jianying-drafts", status_code=202)
    def jianying_draft(plan_id: str, body: JianyingDraftRequest):
        plan = Plan.model_validate(store.get("plan", plan_id, body.revision))
        product = Product.model_validate(store.get("product", plan.product_id))
        assets = {clip.source_asset_id: Asset.model_validate(store.get("asset", clip.source_asset_id))
                  for clip in plan.clips}
        candidates = {clip.candidate_id: Candidate.model_validate(store.get("candidate", clip.candidate_id))
                      for clip in plan.clips}
        validate_plan(plan, product, candidates, assets)
        with store.connection() as conn:
            active = conn.execute("SELECT 1 FROM jobs WHERE kind='jianying' "
                                  "AND json_extract(payload,'$.plan_id')=? "
                                  "AND json_extract(payload,'$.revision')=? "
                                  "AND state IN ('queued','running','pause_requested','paused','cancel_requested') LIMIT 1",
                                  (plan_id, body.revision)).fetchone()
        if active:
            raise HTTPException(409, "该方案修订已有剪映草稿生成任务")
        return store.enqueue("jianying", {"plan_id": plan_id, "revision": body.revision})

    @app.get("/api/v1/exports")
    def exports():
        return store.list("export")

    @app.get("/api/v1/exports/{export_id}/media")
    def export_media(export_id: str):
        artifact = store.get("export", export_id)
        path = Path(artifact["video_path"]).resolve()
        if not any(path.is_relative_to(settings.data_dir / root) for root in ("exports", "previews")) or not path.is_file():
            raise HTTPException(410, "导出文件已移动或删除")
        return FileResponse(path, media_type="video/mp4")

    def cloud_config():
        try:
            return CloudConfig.model_validate(store.get("config", "cloud")["config"])
        except KeyError:
            return CloudConfig()

    @app.get("/api/v1/settings/editing-defaults")
    def get_editing_defaults():
        return editing_defaults(store)

    @app.put("/api/v1/settings/editing-defaults")
    def set_editing_defaults(body: EditingDefaults):
        return save_editing_defaults(store, body)

    @app.get("/api/v1/settings/cloud")
    def get_cloud_config():
        values = {name: read_secret(name) for name in
                  ["llm_api_key", "asr_api_key", "oss_access_key_id", "oss_access_key_secret"]}
        try:
            values["douyin_cookie"] = store.get("config", "douyin_cookie")["value"]
        except KeyError:
            values["douyin_cookie"] = None
        return {"config": cloud_config().model_dump(),
                "secrets": {name: bool(value) for name, value in values.items()},
                "secret_previews": {name: secret_preview(value) for name, value in values.items()}}

    @app.post("/api/v1/settings/cloud/test-model")
    def test_cloud_model(body: ModelConnectionTestRequest):
        return check_model_connection(body)

    @app.patch("/api/v1/settings/cloud")
    def set_cloud_config(body: CloudConfig):
        data = {"id": "cloud", "config": body.model_dump()}
        try:
            store.next_revision("config", "cloud", data)
        except KeyError:
            store.put("config", data)
        return body

    @app.post("/api/v1/settings/secrets")
    def set_secret(body: SecretRequest):
        try:
            if body.name == "douyin_cookie":
                record = {"id": "douyin_cookie", "value": body.value}
                try:
                    store.next_revision("config", "douyin_cookie", record)
                except KeyError:
                    store.put("config", record)
            else:
                write_secret(body.name, body.value)
        except Exception:
            message = "应用设置保存失败，请检查本机数据目录权限" if body.name == "douyin_cookie" else \
                "系统凭据存储不可用，请检查 keyring 依赖"
            raise HTTPException(503, message) from None
        return {"saved": True, "preview": secret_preview(body.value)}

    @app.post("/api/v1/settings/secrets/{name}/reveal")
    def reveal_secret(name: Literal["llm_api_key", "asr_api_key", "oss_access_key_id", "oss_access_key_secret", "douyin_cookie"]):
        if name == "douyin_cookie":
            try:
                value = store.get("config", "douyin_cookie")["value"]
            except KeyError:
                value = None
        else:
            value = read_secret(name)
        if not value:
            raise HTTPException(404, "该密钥尚未配置")
        return {"value": value}

    @app.get("/api/v1/events")
    async def events(request: Request):
        async def stream():
            previous = None
            while not await request.is_disconnected():
                snapshot = await asyncio.to_thread(job_summary, store)
                data = json.dumps(snapshot, ensure_ascii=False)
                if data != previous:
                    yield f"event: jobs\ndata: {data}\n\n"
                    previous = data
                else:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(2)
        return StreamingResponse(stream(), media_type="text/event-stream")

    if frontend_dir is not None:
        from .desktop_frontend import WorkbenchFiles
        app.mount('/', WorkbenchFiles(directory=frontend_dir, html=True), name='workbench')

    return app
