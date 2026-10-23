from __future__ import annotations

import json
import hashlib
import threading
import time
from collections.abc import Callable

from .protocol import (
    ProductChangeEvent,
    decode_push_frame,
    encode_ack_frame,
    iter_fields,
    parse_live_pop_product_id,
    parse_live_shopping_product,
    parse_product_change,
)
from .session import PreparedWebSocketSession


class DouyinProductEventClient:
    """Listen to product-change messages on a caller-provided signed WebSocket URL."""

    def __init__(self, on_event: Callable[[ProductChangeEvent], None], *,
                 on_current_product: Callable[[ProductChangeEvent], None] | None = None,
                 on_state: Callable[[str, str], None] | None = None,
                 cookie: str = "", user_agent: str | None = None,
                 include_samples: bool = True):
        self.on_event = on_event
        self.on_current_product = on_current_product or (lambda _event: None)
        self.on_state = on_state or (lambda _state, _detail: None)
        self.cookie = cookie
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
        self.include_samples = include_samples
        self._app = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._sampled_method_values: dict[str, set[str]] = {}
        self._last_live_shopping_product_id: str | None = None
        self._pending_live_shopping_event: ProductChangeEvent | None = None
        self._pending_live_shopping_deadline = 0.0

    def start(self, signed_url: str):
        if self._thread and self._thread.is_alive():
            raise RuntimeError("product event listener is already running")
        if not signed_url.startswith("wss://"):
            raise ValueError("signed_url must use wss://")

        try:
            import websocket
        except ImportError as exc:
            raise RuntimeError("websocket-client is required; run uv sync") from exc

        headers = [f"User-Agent: {self.user_agent}"]
        if self.cookie:
            headers.append(f"Cookie: {self.cookie}")

        def on_open(_ws):
            self.on_state("listening", "")

        def on_message(ws, message):
            if not isinstance(message, bytes):
                return
            try:
                response = decode_push_frame(message)
                ack_sent = False
                if response.need_ack:
                    ws.send(
                        encode_ack_frame(_extract_log_id(message), response.internal_ext),
                        opcode=websocket.ABNF.OPCODE_BINARY,
                    )
                    ack_sent = True
                received_at = time.time()
                samples = []
                live_pop_product_ids = {
                    product_id
                    for item in response.messages
                    if item.method == "WebcastLiveEcomGeneralMessage"
                    if (product_id := parse_live_pop_product_id(item.payload)) is not None
                }
                pending = self._pending_live_shopping_event
                if pending is not None and received_at > self._pending_live_shopping_deadline:
                    self._pending_live_shopping_event = None
                elif pending is not None and pending.promotion_id in live_pop_product_ids:
                    self.on_event(pending)
                    self._pending_live_shopping_event = None
                for item in response.messages:
                    method_key = item.method.lower()
                    if self.include_samples and any(
                        marker in method_key for marker in ("product", "ecom", "shopping")
                    ):
                        sample = _message_sample(item.method, item.payload, include_values=True)
                        fingerprint = json.dumps(
                            {"fields": sample["fields"], "values": sample["values"]},
                            sort_keys=True,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        value_hash = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
                        seen = self._sampled_method_values.setdefault(item.method, set())
                        if value_hash not in seen and len(seen) < 100:
                            seen.add(value_hash)
                            samples.append(sample)
                    if item.method != "WebcastProductChangeMessage":
                        if item.method == "WebcastLiveShoppingMessage":
                            event = parse_live_shopping_product(item.payload, item.message_id, received_at)
                            if event is not None:
                                self._observe_live_shopping_product(event, live_pop_product_ids)
                        continue
                    for event in parse_product_change(item.payload, item.message_id, received_at):
                        self.on_event(event)
                if self.include_samples:
                    self.on_state("frame_received", json.dumps({
                        "bytes": len(message),
                        "message_count": len(response.messages),
                        "methods": [item.method for item in response.messages],
                        "need_ack": response.need_ack,
                        "ack_sent": ack_sent,
                        "samples": samples,
                    }, ensure_ascii=False, separators=(",", ":")))
            except (ValueError, TypeError) as exc:
                if self.include_samples:
                    prefix = message[:16].hex(" ")
                    self.on_state(
                        "frame_error",
                        f"invalid Douyin WebSocket frame: {exc}; "
                        f"bytes={len(message)}; hex_prefix={prefix}",
                    )

        def on_error(_ws, error):
            self.on_state("error", str(error))

        def on_close(_ws, status_code, message):
            if not self._stop.is_set():
                self.on_state("disconnected", f"{status_code or ''} {message or ''}".strip())

        self._stop.clear()
        self._app = websocket.WebSocketApp(
            signed_url,
            header=headers,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        self._thread = threading.Thread(
            target=lambda: self._app.run_forever(ping_interval=0),
            name="douyin-product-events",
            daemon=True,
        )
        self._thread.start()

    def _observe_live_shopping_product(self, event: ProductChangeEvent, live_pop_product_ids: set[str]):
        if event.received_at > self._pending_live_shopping_deadline:
            self._pending_live_shopping_event = None

        pending = self._pending_live_shopping_event
        if pending is not None and pending.promotion_id in live_pop_product_ids:
            self.on_event(pending)
            self._pending_live_shopping_event = None

        previous_id = self._last_live_shopping_product_id
        self._last_live_shopping_product_id = event.promotion_id
        if previous_id is None:
            self.on_current_product(event)
            return
        if event.promotion_id == previous_id:
            return

        self._pending_live_shopping_event = event
        self._pending_live_shopping_deadline = event.received_at + 5
        if event.promotion_id in live_pop_product_ids:
            self.on_event(event)
            self._pending_live_shopping_event = None

    def start_prepared(self, session: PreparedWebSocketSession):
        """Start listening with the URL and browser headers from a prepared session."""
        if self._thread and self._thread.is_alive():
            raise RuntimeError("product event listener is already running")
        self.cookie = session.cookie
        self.user_agent = session.user_agent
        self.start(session.websocket_url)

    def stop(self, timeout: float = 5):
        self._stop.set()
        if self._app is not None:
            self._app.close()
        if self._thread is not None:
            self._thread.join(timeout)
        self.on_state("stopped", "")


def _extract_log_id(frame: bytes) -> int:
    from .protocol import iter_fields

    return next((int(field.value) for field in iter_fields(frame)
                 if field.number == 2 and field.wire_type == 0), 0)


def _message_sample(method: str, payload: bytes, *, include_values: bool = False) -> dict:
    sample = {
        "method": method,
        "bytes": len(payload),
        "fields": _summarize_fields(payload, depth=0),
    }
    if include_values:
        sample["diagnostic_version"] = 1
        sample["values"] = _collect_values(payload)
    return sample


def _collect_values(payload: bytes, *, path: tuple[int, ...] = (), depth: int = 0) -> list[dict]:
    try:
        parsed = list(iter_fields(payload))
    except ValueError:
        return []

    values = []
    for field in parsed[:32]:
        field_path = (*path, field.number)
        if isinstance(field.value, int):
            values.append({"path": ".".join(map(str, field_path)), "kind": "integer", "value": field.value})
        elif field.value:
            try:
                text = field.value.decode("utf-8")
            except UnicodeDecodeError:
                text = ""
            if text and all(character.isprintable() for character in text):
                values.append({
                    "path": ".".join(map(str, field_path)),
                    "kind": "text",
                    "value": text[:160],
                    **({"truncated": True} if len(text) > 160 else {}),
                })
            elif depth < 8:
                values.extend(_collect_values(field.value, path=field_path, depth=depth + 1))
        if len(values) >= 100:
            return values[:100]
    return values


def _summarize_fields(payload: bytes, *, depth: int) -> list[dict]:
    try:
        parsed = list(iter_fields(payload))
    except ValueError:
        return [{"kind": "opaque", "bytes": len(payload)}]

    fields = []
    for field in parsed[:32]:
        item = {"number": field.number, "wire_type": field.wire_type}
        if isinstance(field.value, int):
            item["kind"] = "integer"
        else:
            item["bytes"] = len(field.value)
            if not field.value:
                item["kind"] = "empty"
            else:
                try:
                    field.value.decode("utf-8")
                    item["kind"] = "text"
                except UnicodeDecodeError:
                    item["kind"] = "protobuf"
                    if depth < 3:
                        item["children"] = _summarize_fields(field.value, depth=depth + 1)
        fields.append(item)
    if len(parsed) > 32:
        fields.append({"kind": "truncated", "remaining_fields": len(parsed) - 32})
    return fields
