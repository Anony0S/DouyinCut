import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import Field

from .core.config import Settings
from .core.logs import ACTIVE_JOB_STATES, LogLibrary, configure_logging
from .core.credentials import read_secret, secret_preview, storage_status, write_secret
from .cleanup import DEFAULT_RETENTION_DAYS, CleanupPreviewChanged, cleanup_unused_files
from .core.domain import Asset, Candidate, Model, Plan, Product, Transcript
from .media.editing import make_batches, remap_subtitles, validate_plan
from .media.processing import diagnostics, probe_stream_timing
from .media.thumbnails import PreviewBusy, ThumbnailCache
from .ai.providers import CloudConfig, ProviderError, asr_api_key, model_api_key
from .ai.connection import (ModelConnectionTestRequest, check_model_connection, OssConnectionTestRequest,
                            check_oss_connection)
from .ai.roughcut import DEFAULT_INSTRUCTIONS, RoughcutRequest
from .core.storage import SessionStopping, Store
from .projects.service import (EditingDefaults, editing_defaults, save_editing_defaults, WorkspaceLayout,
                       workspace_layout, save_workspace_layout, ProjectDraft, delete_plan, delete_project, job_summary,
                       project_detail, project_list, save_draft)
from .live_monitoring import (LiveClipOpen, LivePeriodSplit, LiveProductMerge, LiveMonitorRequest,
                              LiveMonitorSettings, LiveRecordingAction, LiveStore)
from .product_platform import ProductPlatformService
from .product_platform.client import ProductPlatformError
from .product_platform.daduoduo import DaduoduoCatalog
from .product_platform.xingdongli import XingdongliService
from .product_platform.schemas import (AddToShowcaseRequest, LoginRequest, MobileRequest,
                                       RoomPlatformRequest, ScopeRequest, SearchRequest, XingdongliTokenRequest)


_UPLOADED_REFERENCE_ID = re.compile(r"coverref_[0-9a-f]{64}")
_REFERENCE_EXTENSIONS = {".png", ".jpg", ".webp"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".ts", ".m4v"}


class ExportRequest(Model):
    format: Literal["mp4"] = "mp4"


class JianyingDraftRequest(Model):
    revision: int = Field(ge=1)
    cover_id: str | None = Field(default=None, min_length=1, max_length=100)


class CoverGenerationRequest(Model):
    plan_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1)
    reference_id: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=4000)


class ProductImagesRequest(Model):
    asset_id: str = Field(min_length=1, max_length=100)
    product_id: str = Field(min_length=1, max_length=100)
    refresh: bool = False


class LogLocation(Model):
    category: Literal["runtime", "task"]
    name: str | None = Field(None, max_length=200)


class SecretRequest(Model):
    name: Literal["llm_api_key", "image_generation_api_key", "asr_api_key", "oss_access_key_id", "oss_access_key_secret", "douyin_cookie"]
    value: str = Field(max_length=20000)


MAX_RETENTION_DAYS = 2**53 - 1


class CleanupRequest(Model):
    clear_expired_data: bool = False
    retention_days: int = Field(DEFAULT_RETENTION_DAYS, ge=1, le=MAX_RETENTION_DAYS)
    preview_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class LocalVideoRequest(Model):
    path: str = Field(min_length=1, max_length=32767)


def create_app(settings: Settings | None = None, token: str | None = None, *, frontend_dir: Path | None = None):
    settings = settings or Settings.from_env()
    if os.environ.get("DOUYINCUT_LOG_FILE"):
        # The development launcher runs this factory under the uvicorn CLI.
        configure_logging("api")
    settings.prepare()
    store = Store(settings.database)
    live_store = LiveStore(store)
    thumbnails = ThumbnailCache(settings)
    logs = LogLibrary(settings, store)
    token = token or os.environ.get("DOUYINCUT_SESSION_TOKEN") or secrets.token_urlsafe(32)
    # WebView profiles are shared by development and installed workbenches.
    # Cookies are host-scoped, not port-scoped: a fixed name lets another
    # loopback session replace the credential used by <video> range requests.
    session_cookie = f"douyincut_session_{settings.port}"
    catalog = DaduoduoCatalog(store)
    xingdongli = XingdongliService(store)
    active_mutations = 0
    clearing_data = False

    @asynccontextmanager
    async def lifespan(_app):
        try:
            await asyncio.to_thread(catalog.start)
            yield
        finally:
            catalog.close()

    app = FastAPI(title="DouyinCut", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.settings, app.state.live_store = store, settings, live_store
    app.state.product_platform = ProductPlatformService(store)
    allowed = {f"http://127.0.0.1:{settings.port}"}
    if frontend_dir is None:
        allowed.update({"http://127.0.0.1:5173", "http://tauri.localhost", "https://tauri.localhost"})
    else:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1'])
    app.add_middleware(CORSMiddleware, allow_origins=list(allowed), allow_credentials=True,
                       allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Content-Type", "X-Session-Token"])

    @app.middleware("http")
    async def local_auth(request: Request, call_next):
        nonlocal active_mutations
        trace = secrets.token_hex(8)
        mutating = False
        if request.url.path.startswith("/api/") and request.method != "OPTIONS":
            origin = request.headers.get("origin")
            provided = request.headers.get("x-session-token") or request.cookies.get(session_cookie, "")
            if (origin is not None and origin not in allowed) or not hmac.compare_digest(provided.encode(), token.encode()):
                return JSONResponse({"detail": "本地会话未授权，请通过项目启动器打开界面"}, status_code=401)
            mutating = request.method in {"POST", "PUT", "PATCH", "DELETE"}
            if mutating:
                if clearing_data:
                    return JSONResponse({"detail": "正在清空本地业务数据，请稍后再试"}, status_code=423)
                if store.is_shutting_down():
                    return JSONResponse({"detail": "桌面会话正在退出，不再接受新操作"}, status_code=503)
                active_mutations += 1
        try:
            response = await call_next(request)
        finally:
            if mutating:
                active_mutations -= 1
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

    @app.exception_handler(ProductPlatformError)
    async def product_platform_error(request, exc):
        return JSONResponse({"detail": {"code": exc.code, "message": str(exc)}}, status_code=exc.status or 502)

    @app.exception_handler(SessionStopping)
    async def runtime_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.exception_handler(RequestValidationError)
    async def bad_input(request, exc):
        # Validation errors must not echo submitted secrets in their `input` fields.
        return JSONResponse({"detail": "请求参数无效，请检查字段类型、范围和必填项"}, status_code=422)

    @app.get("/health")
    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.post("/api/v1/auth")
    def authenticate(response: Response):
        response.set_cookie(session_cookie, token, httponly=True, samesite="strict", path="/api/v1")
        return {"authenticated": True}

    @app.get("/api/v1/diagnostics")
    def diagnostic():
        return {**diagnostics(settings), "data_dir": str(settings.data_dir),
                "jianying": {"version": "11.6.0", "status": "untested"},
                "douyin_resolver": "streamget", "cloud_integration": "awaiting_live_validation",
                "credentials": storage_status()}

    @app.post("/api/v1/settings/work-directory/open")
    def open_work_directory():
        if not settings.data_dir.is_dir():
            raise HTTPException(409, "工作目录不存在，请检查存储设备及目录位置")
        if os.name != "nt":
            raise HTTPException(409, "打开工作目录仅支持 Windows")
        try:
            os.startfile(str(settings.data_dir.resolve()))
        except OSError as exc:
            raise HTTPException(409, f"打开工作目录失败：{exc}") from None
        return {"opened": True}

    @app.get("/api/v1/settings/cleanup-unused-files")
    def preview_cleanup(clear_expired_data: bool = False,
                        retention_days: int = Query(DEFAULT_RETENTION_DAYS, ge=1, le=MAX_RETENTION_DAYS)):
        return cleanup_unused_files(store, settings.data_dir, retention_days=retention_days,
                                    clear_expired_data=clear_expired_data, preview=True)

    @app.post("/api/v1/settings/cleanup-unused-files")
    async def cleanup_old_unused_files(body: CleanupRequest):
        nonlocal clearing_data
        if body.clear_expired_data:
            if active_mutations != 1 or clearing_data:
                raise HTTPException(423, "其他本地操作正在写入数据，请稍后再试。")
            clearing_data = True
        try:
            return await asyncio.to_thread(cleanup_unused_files, store, settings.data_dir,
                                           retention_days=body.retention_days, clear_expired_data=body.clear_expired_data,
                                           preview_id=body.preview_id)
        except CleanupPreviewChanged:
            raise HTTPException(409, "可清理的文件或记录已变化，请确认重新统计的结果。") from None
        finally:
            if body.clear_expired_data:
                clearing_data = False

    @app.get("/api/v1/logs")
    def list_logs():
        return {"files": logs.files(), "runtime_available": logs.runtime_dir is not None}

    @app.get("/api/v1/logs/{category}/{name}")
    def read_log(category: Literal["runtime", "task"], name: str, after: int | None = Query(None, ge=0)):
        return logs.read(category, name, after)

    @app.post("/api/v1/logs/open")
    def open_log_location(body: LogLocation):
        try:
            return logs.open_location(body.category, body.name)
        except OSError as exc:
            raise HTTPException(409, f"打开日志位置失败：{exc}") from None

    @app.delete("/api/v1/logs")
    def clear_logs():
        return logs.clear()

    @app.get("/api/v1/product-platform/status")
    def product_platform_status():
        return app.state.product_platform.status()

    @app.post("/api/v1/product-platform/send-code")
    def product_platform_send_code(body: MobileRequest):
        return app.state.product_platform.send_code(body.mobile)

    @app.post("/api/v1/product-platform/login")
    def product_platform_login(body: LoginRequest):
        return app.state.product_platform.login(body.mobile, body.sms_code)

    @app.post("/api/v1/product-platform/scope")
    def product_platform_scope(body: ScopeRequest):
        return app.state.product_platform.select_scope(body.main_ip_id)

    @app.post("/api/v1/product-platform/logout")
    def product_platform_logout():
        return app.state.product_platform.logout()

    @app.get("/api/v1/product-platform/daduoduo")
    def daduoduo_settings():
        return catalog.settings()

    @app.get("/api/v1/product-platform/xingdongli")
    def xingdongli_status():
        return xingdongli.status()

    @app.post("/api/v1/product-platform/xingdongli/connect")
    def xingdongli_connect(body: XingdongliTokenRequest):
        return xingdongli.connect(body.member_token)

    @app.post("/api/v1/product-platform/xingdongli/refresh")
    def xingdongli_refresh():
        return xingdongli.connect()

    @app.post("/api/v1/product-platform/xingdongli/logout")
    def xingdongli_logout():
        return xingdongli.logout()

    @app.get("/api/v1/live/monitors/{monitor_id}/product-catalog")
    def daduoduo_catalog_status(monitor_id: str):
        return catalog.status(monitor_id)

    @app.post("/api/v1/live/monitors/{monitor_id}/product-catalog", status_code=202)
    def update_daduoduo_catalog(monitor_id: str):
        return catalog.update(monitor_id)

    def resolve_product_platform(monitor_id=None, asset_id=None, *, reference_images=False):
        if monitor_id is not None and asset_id is not None:
            raise HTTPException(422, "请只传直播间或项目 ID")
        if asset_id is not None:
            monitor_id = store.get("asset", asset_id).get("source_monitor_id")
        if monitor_id is None:
            return "xingdou", None
        try:
            binding = live_store.monitor(monitor_id)["product_platform"]
        except KeyError:
            raise ProductPlatformError("来源直播间已移除，无法查询商品", status=409) from None
        if binding is None:
            raise ProductPlatformError("请先为直播间绑定商品平台", code="ROOM_PLATFORM_REQUIRED", status=409)
        platform = binding["platform"]
        if reference_images and platform != "xingdou":
            raise ProductPlatformError("该直播间绑定的平台暂不支持获取参考图",
                                       code="REFERENCE_IMAGES_UNSUPPORTED", status=409)
        return platform, binding

    @app.post("/api/v1/product-platform/products/search")
    def product_platform_search(body: SearchRequest):
        platform, binding = resolve_product_platform(body.monitor_id, body.asset_id,
                                                     reference_images=body.asset_id is not None)
        if platform == "xingdongli":
            return xingdongli.search(binding["ip_id"], body.keyword, body.start, body.sort_type, body.sort_order)
        if not body.keyword:
            raise HTTPException(422, "商品名称不能为空")
        if platform == "daduoduo":
            return catalog.search(body.monitor_id, body.keyword)
        return {**app.state.product_platform.search(body.keyword), "can_add_to_showcase": True}

    @app.post("/api/v1/product-platform/products/images")
    def product_platform_product_images(body: ProductImagesRequest):
        from .ai.image_generation import fetch_product_image
        from .core.domain import new_id
        if not body.asset_id.startswith("asset_") or not body.asset_id[6:].isalnum():
            raise HTTPException(422, "项目标识无效")
        platform, _ = resolve_product_platform(asset_id=body.asset_id, reference_images=True)
        references = settings.data_dir / "covers" / body.asset_id / "references"
        references.mkdir(parents=True, exist_ok=True)
        cache_key = hashlib.sha256(f"{platform}:{body.product_id}".encode("utf-8")).hexdigest()
        cache_path = references / f"{cache_key}.json"
        if not body.refresh and cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                cached_images = cached.get("images", []) if cached.get("product_id") == body.product_id else []
                result = []
                for item in cached_images:
                    image_id = item.get("id") if isinstance(item, dict) else None
                    if not isinstance(image_id, str) or not image_id.startswith("coverref_") or not image_id[9:].isalnum():
                        result = []
                        break
                    matches = [path for path in references.glob(f"{image_id}.*") if path.is_file()]
                    if len(matches) != 1 or not matches[0].resolve().is_relative_to(references.resolve()):
                        result = []
                        break
                    result.append({"id": image_id, "url": f"/api/v1/assets/{body.asset_id}/cover-references/{image_id}"})
                if result:
                    return {"images": result, "cached": True}
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        urls = app.state.product_platform.product_images(body.product_id)
        result = []
        cache_images = []
        for url in urls:
            try:
                data, content_type = fetch_product_image(url)
            except ValueError as exc:
                raise HTTPException(502, str(exc)) from None
            extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(content_type)
            if extension is None:
                continue
            image_id = new_id("coverref")
            path = references / f"{image_id}{extension}"
            path.write_bytes(data)
            result.append({"id": image_id, "url": f"/api/v1/assets/{body.asset_id}/cover-references/{image_id}"})
            cache_images.append({"id": image_id})
        if not result:
            raise HTTPException(404, "星斗商品没有可用图片")
        temporary_cache_path = cache_path.with_suffix(".json.tmp")
        temporary_cache_path.write_text(json.dumps({"product_id": body.product_id, "images": cache_images}),
                                        encoding="utf-8")
        os.replace(temporary_cache_path, cache_path)
        return {"images": result, "cached": False}

    def local_cover_reference_root(asset_id: str) -> Path:
        if not asset_id.startswith("asset_") or not asset_id[6:].isalnum():
            raise HTTPException(422, "项目标识无效")
        references = settings.data_dir.resolve() / "covers" / asset_id / "references"
        if references.resolve() != references:
            raise HTTPException(409, "项目参考图目录不可用")
        return references

    def local_cover_references(references: Path):
        if not references.is_dir():
            return
        for path in references.iterdir():
            if (path.suffix in _REFERENCE_EXTENSIONS and _UPLOADED_REFERENCE_ID.fullmatch(path.stem)
                    and not path.is_symlink() and path.is_file()):
                yield path

    @app.get("/api/v1/assets/{asset_id}/cover-references")
    def list_uploaded_cover_references(asset_id: str):
        references = local_cover_reference_root(asset_id)
        store.get("asset", asset_id)
        return {"images": [
            {"id": path.stem, "url": f"/api/v1/assets/{asset_id}/cover-references/{path.stem}"}
            for path in sorted(local_cover_references(references))
        ]}

    @app.delete("/api/v1/assets/{asset_id}/cover-references/{image_id}")
    def delete_uploaded_cover_reference(asset_id: str, image_id: str):
        if not _UPLOADED_REFERENCE_ID.fullmatch(image_id):
            raise HTTPException(404, "找不到本地上传的参考图")
        with store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            references = local_cover_reference_root(asset_id)
            paths = [path for path in local_cover_references(references) if path.stem == image_id]
            if not paths:
                return {"deleted": True}
            states = ", ".join("?" for _ in ACTIVE_JOB_STATES)
            if conn.execute(
                f"SELECT 1 FROM jobs WHERE kind='cover' AND state IN ({states}) "
                "AND json_extract(payload,'$.reference_path')=? LIMIT 1",
                (*ACTIVE_JOB_STATES, str(paths[0])),
            ).fetchone():
                raise HTTPException(409, "参考图正在被封面任务使用，请在任务结束后删除")
            try:
                paths[0].unlink(missing_ok=True)
            except OSError:
                raise HTTPException(409, "无法删除参考图，请检查文件是否被占用") from None
        return {"deleted": True}

    @app.post("/api/v1/assets/{asset_id}/cover-references", status_code=201)
    async def upload_cover_reference(asset_id: str, request: Request):
        from .ai.image_generation import MAX_IMAGE_BYTES, image_extension

        if not asset_id.startswith("asset_") or not asset_id[6:].isalnum():
            raise HTTPException(422, "项目标识无效")
        await asyncio.to_thread(store.get, "asset", asset_id)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_IMAGE_BYTES:
                raise HTTPException(413, "参考图片不能超过 20 MB")
            data.extend(chunk)
        extension = image_extension(data)
        if extension is None:
            raise HTTPException(422, "请选择 PNG、JPEG 或 WebP 图片")

        image_id = f"coverref_{hashlib.sha256(data).hexdigest()}"

        def save_reference():
            with store.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                if not conn.execute("SELECT 1 FROM objects WHERE kind='asset' AND id=?",
                                    (asset_id,)).fetchone():
                    raise KeyError(asset_id)
                references = local_cover_reference_root(asset_id)
                references.mkdir(parents=True, exist_ok=True)
                path = references / f"{image_id}{extension}"
                if path.is_symlink() or path.is_junction():
                    raise HTTPException(409, "项目参考图路径不可用")
                temporary = references / f".{image_id}-{secrets.token_hex(8)}.partial"
                try:
                    temporary.write_bytes(data)
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)

        await asyncio.to_thread(save_reference)
        return {"id": image_id, "url": f"/api/v1/assets/{asset_id}/cover-references/{image_id}"}

    @app.get("/api/v1/assets/{asset_id}/cover-references/{image_id}")
    def product_platform_product_image(asset_id: str, image_id: str):
        if not asset_id.startswith("asset_") or not asset_id[6:].isalnum():
            raise HTTPException(404, "找不到图片")
        if not image_id.startswith("coverref_") or not image_id[9:].isalnum():
            raise HTTPException(404, "找不到图片")
        references = (settings.data_dir / "covers" / asset_id / "references").resolve()
        matches = [path for path in references.glob(f"{image_id}.*") if path.is_file()]
        if len(matches) != 1 or not matches[0].resolve().is_relative_to(references):
            raise HTTPException(404, "找不到图片")
        return FileResponse(matches[0])

    @app.post("/api/v1/product-platform/products/add-to-showcase")
    def product_platform_add_to_showcase(body: AddToShowcaseRequest):
        platform, _ = resolve_product_platform(body.monitor_id)
        if platform != "xingdou":
            raise ProductPlatformError("该直播间绑定的平台暂不支持添加橱窗", status=409)
        return app.state.product_platform.add_to_showcase(body.platform_product_id)

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

    @app.post("/api/v1/live/monitors/{monitor_id}/sessions/{session_id}/product-listener/retry", status_code=202)
    def retry_live_product_listener(monitor_id: str, session_id: str):
        try:
            return live_store.request_product_listener_retry(monitor_id, session_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.delete("/api/v1/live/monitors/{monitor_id}/sessions/{session_id}")
    def delete_live_recording_session(monitor_id: str, session_id: str):
        try:
            return live_store.delete_session(
                monitor_id, session_id, settings.data_dir / "recordings" / "live"
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/v1/live/monitors", status_code=201)
    def create_live_monitor(body: LiveMonitorRequest):
        if body.product_platform and body.product_platform.platform == "xingdongli":
            xingdongli.scope(body.product_platform.ip_id)
        return live_store.create_monitor(body.url, body.name, body.product_platform)

    @app.patch("/api/v1/live/monitors/{monitor_id}/product-platform")
    def update_room_product_platform(monitor_id: str, body: RoomPlatformRequest):
        if body.binding and body.binding.platform == "xingdongli":
            xingdongli.scope(body.binding.ip_id)
        return live_store.set_product_platform(monitor_id, body.binding)

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

    @app.post("/api/v1/live/segments/{segment_id}/open")
    def open_live_segment(segment_id: str, body: LiveClipOpen):
        try:
            return live_store.open_segment(
                segment_id, settings.data_dir / "recordings" / "live", reveal=body.reveal)
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

    @app.get("/api/v1/live/product-periods/{period_id}/split-plan")
    def live_period_split_plan(period_id: str):
        return live_store.period_split_plan(period_id)

    @app.get("/api/v1/live/product-periods/{period_id}/thumbnail")
    def live_period_thumbnail(period_id: str, at: float = Query(ge=0, allow_inf_nan=False)):
        period = live_store.period_split_plan(period_id)
        source = next((item for item in period["sources"] if item["start"] <= at < item["end"]), None)
        if source is None or source["state"] != "ready":
            raise HTTPException(409, "该位置尚未封存或录制文件不可用")
        with store.connection() as conn:
            row = conn.execute("SELECT path FROM live_segments WHERE id=?", (source["segment_id"],)).fetchone()
        if row is None:
            raise HTTPException(404, "录制文件已不存在")
        path = Path(row["path"]).resolve()
        if not path.is_relative_to((settings.data_dir / "recordings" / "live").resolve()):
            raise HTTPException(409, "录制文件不在直播录制目录中")
        # One-second buckets stay inside this source/lecture intersection.
        second = source["clip_start"] + int(at - source["start"] + 0.000001)
        try:
            frame = thumbnails.frame(path, second)
        except PreviewBusy:
            raise HTTPException(429, "画面预览繁忙，请稍后重试", headers={"Retry-After": "1"}) from None
        except (OSError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from None
        return Response(frame, media_type="image/jpeg")

    @app.post("/api/v1/live/product-periods/{period_id}/product-clips", status_code=202)
    def split_live_period(period_id: str, body: LivePeriodSplit):
        try:
            return live_store.request_period_product_clips(
                period_id, body.confirmed_missing_segment_ids, body.start, body.end
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/v1/live/sessions/{session_id}/product-clips/merge", status_code=202)
    def merge_live_product_clips(session_id: str, body: LiveProductMerge):
        try:
            return live_store.request_merged_product_clip(
                session_id, body.period_ids, body.confirmed_missing_sources
            )
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
        if suffix not in _VIDEO_EXTENSIONS:
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

    @app.post("/api/v1/assets/import-local", status_code=202)
    def import_local_video(body: LocalVideoRequest):
        source = Path(body.path)
        suffix = source.suffix.lower()
        if not source.is_absolute() or suffix not in _VIDEO_EXTENSIONS:
            raise ValueError("请选择本机支持的视频文件")
        folder = settings.data_dir / "recordings" / "imports"
        final = folder / (secrets.token_hex(16) + suffix)
        temporary = final.with_suffix(suffix + ".partial")
        try:
            initial = source.stat()
            if not stat.S_ISREG(initial.st_mode) or not initial.st_size:
                raise ValueError("所选视频为空文件或无法读取")
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, temporary)
            current = source.stat()
            identity = (initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns)
            if identity != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns):
                raise ValueError("视频仍在写入，请等待文件保存后再导入")
            temporary.rename(final)
            os.utime(final, ns=(initial.st_atime_ns, initial.st_mtime_ns))
            return store.enqueue("import", {"path": str(final), "display_name": source.name,
                                            "original_path": str(source), "original_file_id": identity[:2]})
        except BaseException as exc:
            for owned in (temporary, final):
                try:
                    owned.unlink(missing_ok=True)
                except OSError:
                    pass
            if isinstance(exc, OSError):
                raise HTTPException(409, "无法复制视频，请检查原件状态、权限和剩余空间") from None
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
    def jobs(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
             before_created: float | None = Query(None, ge=0, allow_inf_nan=False),
             before_id: str | None = Query(None, min_length=1, max_length=200)):
        if (before_created is None) != (before_id is None):
            raise HTTPException(422, "任务游标必须同时包含创建时间和任务 ID")
        if before_created is not None and offset:
            raise HTTPException(422, "任务游标不能与偏移量同时使用")
        return store.jobs(limit, offset, before_created, before_id)

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
        payload = {"plan_id": plan_id, "revision": body.revision}
        if body.cover_id:
            cover = store.job(body.cover_id)
            if (cover["kind"] != "cover" or cover["state"] != "succeeded"
                    or cover["payload"].get("plan_id") != plan_id
                    or cover["payload"].get("revision") != body.revision
                    or cover["payload"].get("asset_id") != plan.clips[0].source_asset_id):
                raise HTTPException(422, "所选封面不属于当前方案修订")
            cover_path = Path(cover["result"]["path"]).resolve()
            covers_root = (settings.data_dir / "covers").resolve()
            if not cover_path.is_file() or not cover_path.is_relative_to(covers_root):
                raise HTTPException(410, "所选封面文件已移动或删除")
            payload["cover_path"] = str(cover_path)
        return store.enqueue("jianying", payload)

    @app.post("/api/v1/assets/{asset_id}/covers", status_code=202)
    def generate_cover(asset_id: str, body: CoverGenerationRequest):
        from .ai.providers import ProviderError
        asset = Asset.model_validate(store.get("asset", asset_id))
        plan = Plan.model_validate(store.get("plan", body.plan_id, body.revision))
        if not plan.clips or any(clip.source_asset_id != asset_id for clip in plan.clips):
            raise HTTPException(422, "方案不属于当前视频项目")
        config = cloud_config()
        config.require_image_connection()
        if not read_secret("image_generation_api_key"):
            raise ProviderError("请在云端设置的封面生图卡片配置 API Key")
        reference_id = body.reference_id
        if not reference_id.startswith("coverref_") or not reference_id[9:].isalnum():
            raise HTTPException(422, "参考图片标识无效")
        reference_root = (settings.data_dir / "covers" / asset_id / "references").resolve()
        reference_matches = [path for path in reference_root.glob(f"{reference_id}.*") if path.is_file()]
        if len(reference_matches) != 1 or not reference_matches[0].resolve().is_relative_to(reference_root):
            raise HTTPException(410, "参考图片已移动或删除，请重新获取或上传参考图")
        reference_path = reference_matches[0].resolve()
        try:
            return store.enqueue("cover", {
                "asset_id": asset.id, "plan_id": plan.id, "revision": plan.revision,
                "reference_path": str(reference_path), "prompt": body.prompt.strip(),
                "config": config.model_dump(),
            }, required_file=reference_path)
        except FileNotFoundError:
            raise HTTPException(410, "参考图片已移动或删除，请重新获取或上传参考图") from None

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

    @app.get("/api/v1/assets/{asset_id}/covers/{cover_id}/media")
    def generated_cover_media(asset_id: str, cover_id: str):
        job = store.job(cover_id)
        if job["kind"] != "cover" or job["state"] != "succeeded" or job["payload"].get("asset_id") != asset_id:
            raise HTTPException(404, "找不到封面")
        path = Path(job["result"]["path"]).resolve()
        root = (settings.data_dir / "covers" / asset_id).resolve()
        if not path.is_file() or not path.is_relative_to(root):
            raise HTTPException(410, "封面文件已移动或删除")
        return FileResponse(path, media_type=job["result"].get("content_type", "image/png"))

    @app.get("/api/v1/assets/{asset_id}/covers/{cover_id}/original")
    def generated_cover_original(asset_id: str, cover_id: str):
        job = store.job(cover_id)
        if job["kind"] != "cover" or job["state"] != "succeeded" or job["payload"].get("asset_id") != asset_id:
            raise HTTPException(404, "找不到封面")
        original = job["result"].get("original_path")
        if not isinstance(original, str) or not original:
            raise HTTPException(410, "原图不可用，请重新生成封面")
        path = Path(original).resolve()
        root = (settings.data_dir / "covers" / asset_id).resolve()
        if not path.is_file() or not path.is_relative_to(root):
            raise HTTPException(410, "原图已移动或删除，请重新生成封面")
        return FileResponse(path, media_type=job["result"].get("original_content_type", "application/octet-stream"))

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

    @app.get("/api/v1/settings/workspace-layout")
    def get_workspace_layout():
        return workspace_layout(store)

    @app.put("/api/v1/settings/workspace-layout")
    def set_workspace_layout(body: WorkspaceLayout):
        return save_workspace_layout(store, body)

    @app.get("/api/v1/settings/cloud")
    def get_cloud_config():
        values = {name: read_secret(name) for name in
                  ["llm_api_key", "image_generation_api_key", "asr_api_key", "oss_access_key_id", "oss_access_key_secret"]}
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

    @app.post("/api/v1/settings/cloud/test-oss")
    def test_cloud_oss(body: OssConnectionTestRequest):
        return check_oss_connection(body)

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
    def reveal_secret(name: Literal["llm_api_key", "image_generation_api_key", "asr_api_key", "oss_access_key_id", "oss_access_key_secret", "douyin_cookie"]):
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
