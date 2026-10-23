from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from .client import DouyinProductEventClient
from .session import DouyinWebcastSessionPreparer, PreparedWebSocketSession


def _safe_websocket_url(session: PreparedWebSocketSession) -> str:
    parts = urlsplit(session.websocket_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "[query omitted]", ""))


def probe(room_url: str, *, cookie: str = "", listen_seconds: float = 30) -> int:
    try:
        preparer = DouyinWebcastSessionPreparer(cookie=cookie)
    except Exception as exc:
        print(json.dumps({
            "stage": "http_client_setup",
            "error_type": type(exc).__name__,
            "detail": _safe_exception(exc),
        }, ensure_ascii=False), flush=True)
        return 1
    try:
        session = preparer.prepare(room_url)
    except Exception as exc:
        print(json.dumps({
            "stage": "session_prepare",
            "error_type": type(exc).__name__,
            "detail": _safe_exception(exc),
        }, ensure_ascii=False), flush=True)
        return 1
    finally:
        preparer.close()

    print(json.dumps({
        "stage": "session_prepared",
        "live_id": session.room.live_id,
        "room_id": session.room.room_id,
        "anchor_name": session.room.anchor_name,
        "title": session.room.title,
        "is_live": session.room.is_live,
        "push_host": urlsplit(session.websocket_url).hostname,
        "websocket_url": _safe_websocket_url(session),
        "cookie_sent": bool(session.cookie),
    }, ensure_ascii=False, indent=2), flush=True)

    connected = threading.Event()
    disconnected = threading.Event()
    events = 0
    frame_errors = 0
    frames_received = 0
    decoded_messages = 0
    acknowledgements = 0
    message_methods: dict[str, int] = {}
    sample_text_seen: set[str] = set()

    def on_state(state: str, detail: str):
        nonlocal events, frame_errors, frames_received, decoded_messages, acknowledgements
        if state == "listening":
            connected.set()
            print(json.dumps({
                "stage": "websocket",
                "state": state,
                "detail": _safe_text(detail),
            }, ensure_ascii=False), flush=True)
        elif state == "frame_received":
            frames_received += 1
            try:
                info = json.loads(detail)
            except (TypeError, json.JSONDecodeError):
                info = {}
            decoded_messages += int(info.get("message_count", 0))
            acknowledgements += int(bool(info.get("ack_sent")))
            for method in info.get("methods", []):
                message_methods[method] = message_methods.get(method, 0) + 1
            samples = []
            for sample in info.get("samples", []):
                text_values = [
                    {"path": value.get("path"), "value": value.get("value")}
                    for value in sample.get("values", [])
                    if value.get("kind") == "text"
                ]
                if not text_values:
                    continue
                encoded_text = json.dumps(text_values, ensure_ascii=False, sort_keys=True)
                if encoded_text in sample_text_seen:
                    continue
                sample_text_seen.add(encoded_text)
                samples.append(sample)
            if frames_received <= 5 or samples:
                info["samples"] = samples
                print(json.dumps({
                    "stage": "websocket",
                    "state": state,
                    "detail": info,
                }, ensure_ascii=False), flush=True)
        elif state == "frame_error":
            frame_errors += 1
            if frame_errors <= 5:
                print(json.dumps({
                    "stage": "websocket",
                    "state": state,
                    "detail": _safe_text(detail),
                }, ensure_ascii=False), flush=True)
        elif state in {"error", "disconnected"}:
            disconnected.set()
            print(json.dumps({
                "stage": "websocket",
                "state": state,
                "detail": _safe_text(detail),
            }, ensure_ascii=False), flush=True)

    def on_event(event):
        nonlocal events
        events += 1
        print(json.dumps({
            "stage": "product_event",
            "message_id": event.message_id,
            "event_time": event.event_time,
            "received_at": event.received_at,
            "promotion_id": event.promotion_id,
            "product_index": event.product_index,
            "explain_type": event.explain_type,
        }, ensure_ascii=False), flush=True)

    client = DouyinProductEventClient(on_event, on_state=on_state)
    client.start_prepared(session)
    deadline = time.monotonic() + listen_seconds
    try:
        while time.monotonic() < deadline and not disconnected.is_set():
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()

    if not connected.is_set():
        print("WebSocket 未进入 listening 状态。", flush=True)
        return 2
    print(json.dumps({
        "stage": "complete",
        "product_events": events,
        "websocket_frames": frames_received,
        "decoded_messages": decoded_messages,
        "message_methods": message_methods,
        "product_change_messages": message_methods.get("WebcastProductChangeMessage", 0),
        "acknowledgements": acknowledgements,
        "frame_errors": frame_errors,
    }, ensure_ascii=False))
    return 0


def _safe_exception(exc: Exception) -> str:
    if hasattr(exc, "stage") and hasattr(exc, "body_preview"):
        return json.dumps({
            "stage": exc.stage,
            "endpoint": _safe_url(exc.endpoint),
            "http_status": exc.status_code,
            "content_type": exc.content_type,
            "request_id": exc.request_id or None,
            "reason": str(exc),
            "response_preview": _safe_text(exc.body_preview),
        }, ensure_ascii=False)
    response = getattr(exc, "response", None)
    if response is not None:
        return json.dumps({
            "status_code": response.status_code,
            "url": _safe_url(str(response.request.url)),
            "body_preview": _safe_text(response.text)[:2000],
        }, ensure_ascii=False)
    request = getattr(exc, "request", None)
    detail = str(exc)
    if request is not None:
        detail = f"{type(exc).__name__}: {_safe_url(str(request.url))}"
    return detail[:2000]


def _safe_url(value: str) -> str:
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "[query omitted]", ""))


def _safe_text(value: str) -> str:
    return re.sub(r"((?:https?|wss?)://[^\s?]+)\?[^\s]+", r"\1?[query omitted]", value)[:2000]


def main() -> int:
    parser = argparse.ArgumentParser(description="离线外的抖音商品事件会话探针，不接入生产监控")
    parser.add_argument("room_url", help="抖音直播间链接或分享短链")
    parser.add_argument("--listen-seconds", type=float, default=30)
    args = parser.parse_args()
    if args.listen_seconds <= 0 or args.listen_seconds > 3600:
        parser.error("--listen-seconds 必须在 0 到 3600 秒之间")
    return probe(args.room_url, cookie=os.environ.get("DOUYIN_COOKIE", ""),
                 listen_seconds=args.listen_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
