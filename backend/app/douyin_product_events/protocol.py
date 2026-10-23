from __future__ import annotations

import gzip
import time
from dataclasses import dataclass


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class WireField:
    number: int
    wire_type: int
    value: int | bytes


@dataclass(frozen=True)
class PushMessage:
    method: str
    payload: bytes
    message_id: int


@dataclass(frozen=True)
class PushResponse:
    messages: tuple[PushMessage, ...]
    need_ack: bool
    internal_ext: str


@dataclass(frozen=True)
class ProductChangeEvent:
    message_id: int
    received_at: float
    event_time: float
    promotion_id: str
    product_index: int | None
    explain_type: int | None
    source: str = "product_change"


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data) and shift < 70:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    raise ProtocolError("invalid or truncated protobuf varint")


def iter_fields(data: bytes):
    offset = 0
    while offset < len(data):
        key_offset = offset
        key, offset = _read_varint(data, offset)
        number, wire_type = key >> 3, key & 7
        if number == 0:
            raise ProtocolError("protobuf field number must be positive")
        if wire_type == 0:
            value, offset = _read_varint(data, offset)
        elif wire_type == 1:
            end = offset + 8
            if end > len(data):
                raise ProtocolError("truncated fixed64 protobuf field")
            value, offset = data[offset:end], end
        elif wire_type == 2:
            size, offset = _read_varint(data, offset)
            end = offset + size
            if end > len(data):
                raise ProtocolError("truncated length-delimited protobuf field")
            value, offset = data[offset:end], end
        elif wire_type == 5:
            end = offset + 4
            if end > len(data):
                raise ProtocolError("truncated fixed32 protobuf field")
            value, offset = data[offset:end], end
        else:
            nearby = data[max(0, key_offset - 4):min(len(data), offset + 8)].hex(" ")
            raise ProtocolError(
                f"unsupported protobuf wire type: {wire_type} "
                f"(field={number}, key=0x{key:x}, offset={key_offset}, "
                f"near={nearby})"
            )
        yield WireField(number, wire_type, value)


def _first(fields: list[WireField], number: int, wire_type: int):
    return next((field.value for field in fields
                 if field.number == number and field.wire_type == wire_type), None)


def _as_text(value: int | bytes | None) -> str:
    if value is None:
        return ""
    if not isinstance(value, bytes):
        raise ProtocolError("expected a protobuf string field")
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("protobuf string is not valid UTF-8") from exc


def decode_push_frame(data: bytes) -> PushResponse:
    """Decode a binary Douyin PushFrame and its (optionally gzipped) Response."""
    frame_fields = list(iter_fields(data))
    payload = _first(frame_fields, 8, 2)
    if not isinstance(payload, bytes):
        raise ProtocolError("PushFrame has no binary payload")
    encoding = _as_text(_first(frame_fields, 6, 2)).lower()
    if encoding == "gzip" or payload.startswith(b"\x1f\x8b"):
        try:
            payload = gzip.decompress(payload)
        except OSError as exc:
            raise ProtocolError("PushFrame payload is not valid gzip data") from exc

    response_fields = list(iter_fields(payload))
    messages: list[PushMessage] = []
    for field in response_fields:
        if field.number != 1 or field.wire_type != 2 or not isinstance(field.value, bytes):
            continue
        message_fields = list(iter_fields(field.value))
        method = _as_text(_first(message_fields, 1, 2))
        message_payload = _first(message_fields, 2, 2)
        message_id = _first(message_fields, 3, 0)
        if method and isinstance(message_payload, bytes):
            messages.append(PushMessage(method, message_payload, int(message_id or 0)))

    need_ack = bool(_first(response_fields, 9, 0) or 0)
    internal_ext = _as_text(_first(response_fields, 5, 2))
    return PushResponse(tuple(messages), need_ack, internal_ext)


def _encode_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("varint must be non-negative")
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def _encode_bytes_field(number: int, value: bytes) -> bytes:
    return _encode_varint((number << 3) | 2) + _encode_varint(len(value)) + value


def encode_ack_frame(log_id: int, internal_ext: str) -> bytes:
    """Encode the PushFrame ACK shape used by the public Douyin clients."""
    return (_encode_varint(2 << 3) + _encode_varint(log_id)
            + _encode_bytes_field(7, internal_ext.encode("utf-8")))


def _signed_int64(value: int) -> int:
    return value - (1 << 64) if value >= (1 << 63) else value


def parse_product_change(payload: bytes, message_id: int,
                         received_at: float | None = None) -> list[ProductChangeEvent]:
    """Parse ProductChangeMessage fields 2 and 4 plus ProductInfo fields 1, 2, 4."""
    received_at = time.time() if received_at is None else received_at
    fields = list(iter_fields(payload))
    timestamp_ms = _first(fields, 2, 0)
    event_time = float(timestamp_ms) / 1000 if timestamp_ms else received_at
    if abs(event_time - received_at) > 300:
        event_time = received_at

    events: list[ProductChangeEvent] = []
    for field in fields:
        if field.number != 4 or field.wire_type != 2 or not isinstance(field.value, bytes):
            continue
        product_fields = list(iter_fields(field.value))
        promotion_id = _first(product_fields, 1, 0)
        if promotion_id is None:
            continue
        index = _first(product_fields, 2, 0)
        explain_type = _first(product_fields, 4, 0)
        events.append(ProductChangeEvent(
            message_id=message_id,
            received_at=received_at,
            event_time=event_time,
            promotion_id=str(_signed_int64(int(promotion_id))),
            product_index=int(index) if index is not None else None,
            explain_type=_signed_int64(int(explain_type)) if explain_type is not None else None,
        ))
    return events


def parse_live_shopping_product(payload: bytes, message_id: int,
                                received_at: float | None = None) -> ProductChangeEvent | None:
    """Read the observed product id and common timestamp from LiveShoppingMessage."""
    received_at = time.time() if received_at is None else received_at
    try:
        fields = list(iter_fields(payload))
        promotion_id = _first(fields, 3, 0)
        if promotion_id is None:
            return None
        promotion_id = _signed_int64(int(promotion_id))
        if promotion_id <= 0:
            return None

        common = _first(fields, 1, 2)
        common_fields = list(iter_fields(common)) if isinstance(common, bytes) else []
        timestamp_ms = _first(common_fields, 4, 0)
        event_time = float(timestamp_ms) / 1000 if timestamp_ms else received_at
        if abs(event_time - received_at) > 300:
            event_time = received_at
        return ProductChangeEvent(
            message_id=message_id,
            received_at=received_at,
            event_time=event_time,
            promotion_id=str(promotion_id),
            product_index=None,
            explain_type=None,
            source="live_shopping",
        )
    except (ProtocolError, TypeError, ValueError):
        return None


def parse_live_pop_product_id(payload: bytes) -> str | None:
    """Read the product id from the observed LivePopMessage payload."""
    try:
        fields = list(iter_fields(payload))
        if _as_text(_first(fields, 2, 2)) != "LivePopMessage":
            return None
        body = _first(fields, 6, 2)
        if not isinstance(body, bytes):
            return None
        promotion_id = _first(list(iter_fields(body)), 3, 0)
        if promotion_id is None:
            return None
        promotion_id = _signed_int64(int(promotion_id))
        return str(promotion_id) if promotion_id > 0 else None
    except (ProtocolError, TypeError, ValueError):
        return None
