from __future__ import annotations

import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from urllib.parse import quote, urlencode, urljoin, urlparse, urlsplit, urlunsplit

import httpx

from .protocol import iter_fields
from .signing import WebSocketSignatureGenerator
from .webcast_http import a_bogus


LIVE_ORIGIN = "https://live.douyin.com"
WEBCAST_AID = "6383"
WEBCAST_VERSION = "180800"
WEBCAST_SDK_VERSION = "1.0.15"
DEFAULT_CURSOR = "d-1_u-1_fh-7383731312643626035_t-1719159695790_r-1"
DEFAULT_PUSH_URL = "wss://webcast100-ws-web-lf.douyin.com/webcast/im/push/v2/"
DEFAULT_WRDS_VERSION = "7382620942951772256"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class RoomInfo:
    live_id: str
    room_id: str
    user_unique_id: str
    anchor_name: str = ""
    title: str = ""
    is_live: bool | None = None


@dataclass(frozen=True)
class InitialIMState:
    push_url: str
    cursor: str
    internal_ext: str
    heartbeat_duration: int = 0


@dataclass(frozen=True)
class PreparedWebSocketSession:
    room: RoomInfo
    websocket_url: str
    cookie: str
    user_agent: str
    initial_state: InitialIMState


class SessionPreparationError(RuntimeError):
    def __init__(self, *, stage: str, endpoint: str, status_code: int,
                 content_type: str, request_id: str, body_preview: str,
                 reason: str):
        super().__init__(reason)
        self.stage = stage
        self.endpoint = endpoint
        self.status_code = status_code
        self.content_type = content_type
        self.request_id = request_id
        self.body_preview = body_preview


def normalize_live_id(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 128 or not all(
        char.isascii() and (char.isalnum() or char in "_-") for char in value
    ):
        raise ValueError("抖音直播间标识无效")
    return value


def normalize_cookie_header(value: str) -> str:
    value = value.strip()
    if value.lower().startswith("cookie:"):
        value = value.split(":", 1)[1].strip()
    return value


def live_id_from_url(value: str) -> str:
    if "://" not in value and "/" not in value:
        return normalize_live_id(value)
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if parsed.hostname == "live.douyin.com":
        segment = parsed.path.strip("/").split("/", 1)[0]
        if segment:
            return normalize_live_id(segment)
    raise ValueError("无法从抖音直播链接中提取直播间标识")


def _is_douyin_host(hostname: str | None) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host == "douyin.com" or host.endswith(".douyin.com")


def parse_room_enter_response(payload: bytes | str, live_id: str) -> RoomInfo:
    import json

    data = json.loads(payload)
    if not isinstance(data, dict) or int(data.get("status_code", 0) or 0) != 0:
        raise ValueError("抖音 web/enter 返回错误或无效 JSON")
    room_id = _first_value(data, (
        ("data", "data", 0, "id_str"),
        ("data", "data", 0, "id"),
        ("data", "room", "id_str"),
        ("data", "room", "id"),
        ("data", "enter_room_id"),
    ))
    user_id = _first_value(data, (
        ("data", "user", "id_str"),
        ("data", "user", "id"),
        ("data", "data", 0, "owner_user_id_str"),
        ("data", "data", 0, "owner", "id_str"),
        ("data", "data", 0, "owner", "id"),
        ("data", "room", "owner_user_id_str"),
        ("data", "room", "owner", "id_str"),
        ("data", "room", "owner", "id"),
    ))
    anchor_name = _first_value(data, (
        ("data", "user", "nickname"),
        ("data", "data", 0, "owner", "nickname"),
        ("data", "room", "owner", "nickname"),
    ))
    title = _first_value(data, (("data", "data", 0, "title"), ("data", "room", "title")))
    status = _first_value(data, (("data", "data", 0, "status"), ("data", "room", "status")))
    if not room_id or not user_id:
        raise ValueError("web/enter 响应缺少 room_id 或主播 user_unique_id")
    return RoomInfo(
        live_id=normalize_live_id(live_id),
        room_id=str(room_id),
        user_unique_id=str(user_id),
        anchor_name=str(anchor_name or ""),
        title=str(title or ""),
        is_live=int(status) == 2 if status is not None else None,
    )


def build_room_enter_query(live_id: str, user_agent: str, ms_token: str = "",
                           screen_width: int = 1920, screen_height: int = 1080) -> str:
    live_id = normalize_live_id(live_id)
    parts = (
        ("aid", WEBCAST_AID), ("app_name", "douyin_web"), ("live_id", "1"),
        ("device_platform", "web"), ("language", "zh-CN"),
        ("browser_language", "zh-CN"), ("browser_platform", "Win32"),
        ("browser_name", "Chrome"), ("browser_version", _chrome_version(user_agent)),
        ("web_rid", live_id), ("is_need_double_stream", "false"), ("msToken", ms_token),
    )
    return urlencode(parts)


def build_initial_im_query(room: RoomInfo, user_agent: str, ms_token: str,
                           screen_width: int = 1920, screen_height: int = 1080) -> str:
    browser_version = _browser_version(user_agent)
    parts = (
        ("resp_content_type", "protobuf"), ("did_rule", "3"), ("device_id", ""),
        ("app_name", "douyin_web"), ("endpoint", "live_pc"), ("support_wrds", "1"),
        ("user_unique_id", room.user_unique_id), ("identity", "audience"),
        ("need_persist_msg_count", "15"), ("insert_task_id", ""), ("live_reason", ""),
        ("room_id", room.room_id), ("version_code", WEBCAST_VERSION), ("last_rtt", "0"),
        ("live_id", "1"), ("aid", WEBCAST_AID), ("fetch_rule", "1"),
        ("cursor", ""), ("internal_ext", ""), ("device_platform", "web"),
        ("cookie_enabled", "true"), ("screen_width", str(screen_width)),
        ("screen_height", str(screen_height)), ("browser_language", "zh-CN"),
        ("browser_platform", "Win32"), ("browser_name", "Mozilla"),
        ("browser_version", browser_version), ("browser_online", "true"),
        ("tz_name", "Asia/Shanghai"), ("msToken", ms_token),
    )
    return "&".join(f"{key}={_query_escape(value)}" for key, value in parts)


def parse_initial_im_response(payload: bytes, room: RoomInfo, *, now_ms: int | None = None) -> InitialIMState:
    fields = list(iter_fields(payload))
    text = {
        field.number: bytes(field.value).decode("utf-8", errors="replace")
        for field in fields
        if field.wire_type == 2 and field.number in {2, 5, 10, 13, 14}
    }
    heartbeat = next((int(field.value) for field in fields
                      if field.number == 8 and field.wire_type == 0), 0)
    push_url = ""
    for number in (14, 10, 13):
        push_url = normalize_push_url(text.get(number, ""))
        if push_url:
            break
    if not push_url:
        push_url = DEFAULT_PUSH_URL
    fetch_ms = int(time.time() * 1000) if now_ms is None else now_ms
    cursor = text.get(2, "") or DEFAULT_CURSOR
    internal_ext = text.get(5, "") or _default_internal_ext(room, fetch_ms)
    return InitialIMState(push_url, cursor, internal_ext, heartbeat)


def normalize_push_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    value = value.split(",", 1)[0].split(";", 1)[0].rstrip("/")
    if value.startswith("https://"):
        value = "wss://" + value[len("https://"):]
    elif value.startswith("http://"):
        value = "ws://" + value[len("http://"):]
    elif not value.startswith(("wss://", "ws://")):
        value = "wss://" + value
    if "/webcast/im/push/v2" not in value:
        value += "/webcast/im/push/v2"
    return value + "/"


def build_websocket_url(room: RoomInfo, state: InitialIMState, user_agent: str,
                        signer: WebSocketSignatureGenerator, *,
                        screen_width: int = 1920, screen_height: int = 1080) -> str:
    signature = signer.sign(room.room_id, room.user_unique_id)
    browser_version = _browser_version(user_agent)
    parts = (
        ("app_name", "douyin_web"), ("version_code", WEBCAST_VERSION),
        ("webcast_sdk_version", WEBCAST_SDK_VERSION),
        ("update_version_code", WEBCAST_SDK_VERSION), ("compress", "gzip"),
        ("device_platform", "web"), ("cookie_enabled", "true"),
        ("screen_width", str(screen_width)), ("screen_height", str(screen_height)),
        ("browser_language", "zh-CN"), ("browser_platform", "Win32"),
        ("browser_name", "Mozilla"), ("browser_version", browser_version),
        ("browser_online", "true"), ("tz_name", "Asia/Shanghai"),
        ("cursor", state.cursor), ("internal_ext", state.internal_ext),
        ("host", LIVE_ORIGIN), ("aid", WEBCAST_AID), ("live_id", "1"),
        ("did_rule", "3"), ("endpoint", "live_pc"), ("support_wrds", "1"),
        ("user_unique_id", room.user_unique_id),
        ("im_path", "/webcast/im/fetch/"), ("identity", "audience"),
        ("need_persist_msg_count", "15"), ("insert_task_id", ""),
        ("live_reason", ""), ("room_id", room.room_id),
        ("heartbeatDuration", "0"), ("signature", signature),
    )
    return state.push_url + "?" + "&".join(
        f"{key}={_query_escape(value)}" for key, value in parts
    )


class DouyinWebcastSessionPreparer:
    """Resolve room metadata and build a signed WebSocket URL without starting a listener."""

    def __init__(self, *, cookie: str = "", user_agent: str = DEFAULT_USER_AGENT,
                 timeout: float = 20, client: httpx.Client | None = None):
        self.cookie = normalize_cookie_header(cookie)
        self.user_agent = user_agent
        self.timeout = timeout
        self._client = client or httpx.Client(follow_redirects=False, timeout=timeout, http2=True)
        self._owns_client = client is None
        self._signer = WebSocketSignatureGenerator()

    def prepare(self, room_url: str) -> PreparedWebSocketSession:
        live_id = self._resolve_live_id(room_url)
        self._prime_room_page(live_id)
        cookie = self.cookie or self._cookie_header()
        ms_token = _cookie_value(cookie, "msToken")
        room_query = build_room_enter_query(live_id, self.user_agent, ms_token)
        room_response = self._get_signed(
            f"{LIVE_ORIGIN}/webcast/room/web/enter/", room_query, f"{LIVE_ORIGIN}/{live_id}"
        )
        try:
            room = parse_room_enter_response(room_response.content, live_id)
        except (ValueError, TypeError) as exc:
            raise _response_error("room_enter", room_response, str(exc)) from exc
        if room.is_live is False:
            raise ValueError("抖音返回该直播间当前未开播")

        fetch_query = build_initial_im_query(room, self.user_agent, ms_token)
        fetch_response = self._get_signed(
            f"{LIVE_ORIGIN}/webcast/im/fetch/", fetch_query, f"{LIVE_ORIGIN}/{live_id}"
        )
        try:
            state = parse_initial_im_response(fetch_response.content, room)
        except (ValueError, TypeError) as exc:
            raise _response_error("initial_im", fetch_response, str(exc)) from exc
        websocket_url = build_websocket_url(room, state, self.user_agent, self._signer)
        cookie = self.cookie or self._cookie_header()
        return PreparedWebSocketSession(room, websocket_url, cookie, self.user_agent, state)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _resolve_live_id(self, room_url: str) -> str:
        normalized = room_url if "://" in room_url else f"https://{room_url}"
        parsed = urlparse(normalized)
        if parsed.scheme != "https" or not _is_douyin_host(parsed.hostname):
            raise ValueError("仅支持 HTTPS 抖音直播间链接或抖音分享短链")
        if parsed.hostname == "live.douyin.com":
            return live_id_from_url(normalized)
        current = normalized
        for _ in range(10):
            parsed = urlparse(current)
            if parsed.scheme != "https" or not _is_douyin_host(parsed.hostname):
                raise ValueError("抖音分享链接跳转到了非 HTTPS 抖音域名，已停止请求")
            response = self._client.get(current, headers=self._headers(f"{LIVE_ORIGIN}/"))
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("抖音分享短链返回了没有目标地址的重定向")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            resolved = str(response.url)
            if not _is_douyin_host(urlparse(resolved).hostname):
                raise ValueError("抖音分享链接跳转到了非抖音域名，已停止请求")
            return live_id_from_url(resolved)
        raise ValueError("抖音分享短链重定向次数过多")

    def _prime_room_page(self, live_id: str) -> None:
        current = f"{LIVE_ORIGIN}/{live_id}"
        for _ in range(10):
            response = self._client.get(current, headers=self._headers(f"{LIVE_ORIGIN}/"))
            if not response.is_redirect:
                response.raise_for_status()
                if not _is_douyin_host(urlparse(str(response.url)).hostname):
                    raise ValueError("直播间页面跳转到了非抖音域名，已停止请求")
                return
            location = response.headers.get("location")
            if not location:
                raise ValueError("直播间页面返回了没有目标地址的重定向")
            current = urljoin(current, location)
            parsed = urlparse(current)
            if parsed.scheme != "https" or not _is_douyin_host(parsed.hostname):
                raise ValueError("直播间页面跳转到了非 HTTPS 抖音域名，已停止请求")
        raise ValueError("直播间页面重定向次数过多")

    def _get_signed(self, endpoint: str, query: str, referer: str) -> httpx.Response:
        signed = f"{endpoint}?{query}&a_bogus={_query_escape(a_bogus(query, self.user_agent))}"
        headers = self._headers(referer)
        headers["Accept-Encoding"] = "identity"
        if endpoint.endswith("/webcast/im/fetch/"):
            headers["Accept"] = "*/*"
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        response = self._client.get(signed, headers=headers)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            stage = "initial_im" if endpoint.endswith("/webcast/im/fetch/") else "room_enter"
            raise _response_error(stage, response, "HTTP 请求被拒绝") from exc
        return response

    def _headers(self, referer: str) -> dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": referer,
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    def _cookie_header(self) -> str:
        return "; ".join(f"{cookie.name}={cookie.value}" for cookie in self._client.cookies.jar)


def _first_value(data: object, paths: tuple[tuple[object, ...], ...]) -> object | None:
    for path in paths:
        value = data
        for key in path:
            if isinstance(value, dict):
                value = value.get(key)
            elif isinstance(value, list) and isinstance(key, int) and key < len(value):
                value = value[key]
            else:
                value = None
                break
        if value not in (None, ""):
            return value
    return None


def _response_error(stage: str, response: httpx.Response, reason: str) -> SessionPreparationError:
    content_type = response.headers.get("content-type", "")
    request_id = next((response.headers.get(name, "") for name in (
        "x-tt-logid", "x-tt-trace-id", "x-tt-request-id", "request-id",
    ) if response.headers.get(name)), "")
    if "protobuf" in content_type.lower() or "octet-stream" in content_type.lower():
        body_preview = response.content[:96].hex(" ")
        body_preview = f"hex: {body_preview}" if body_preview else "<empty>"
    else:
        body_preview = response.text[:2000] or "<empty>"
    request_url = urlsplit(str(response.request.url))
    endpoint = urlunsplit((request_url.scheme, request_url.netloc, request_url.path, "", ""))
    return SessionPreparationError(
        stage=stage,
        endpoint=endpoint,
        status_code=response.status_code,
        content_type=content_type,
        request_id=request_id,
        body_preview=body_preview,
        reason=reason,
    )


def _cookie_value(cookie: str, key: str) -> str:
    parsed = SimpleCookie()
    parsed.load(cookie)
    morsel = parsed.get(key)
    return morsel.value if morsel else ""


def _browser_version(user_agent: str) -> str:
    return user_agent.split("Mozilla/", 1)[-1]


def _chrome_version(user_agent: str) -> str:
    marker = "Chrome/"
    if marker in user_agent:
        return user_agent.split(marker, 1)[1].split(" ", 1)[0]
    return _browser_version(user_agent)


def _query_escape(value: str, *, spaces_only: bool = False) -> str:
    if spaces_only:
        return value.replace(" ", "%20")
    return quote(value, safe="-_.~")


def _default_internal_ext(room: RoomInfo, now_ms: int) -> str:
    return (
        f"internal_src:dim|wss_push_room_id:{room.room_id}|wss_push_did:{room.user_unique_id}"
        f"|first_req_ms:{now_ms}|fetch_time:{now_ms}|seq:1|wss_info:0-{now_ms}-0-0"
        f"|wrds_v:{DEFAULT_WRDS_VERSION}"
    )
