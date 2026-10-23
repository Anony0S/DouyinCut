from __future__ import annotations

import base64
import hashlib
import secrets
import threading
from collections.abc import Callable


_CUSTOM_BASE64 = "Dkdpgh4ZKsQB80/Mfvw36XI1R25+WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe"
_STANDARD_BASE64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_BASE64_TRANSLATION = str.maketrans(_STANDARD_BASE64, _CUSTOM_BASE64)
_EMPTY_BODY_DIGEST = hashlib.md5(hashlib.md5(b"").digest()).digest()


def websocket_signature_stub(room_id: str, user_unique_id: str) -> str:
    """Return the X-MS-STUB input for douyinLive v2.3.1's WebSocket signer."""
    room_id = str(room_id).strip()
    user_unique_id = str(user_unique_id).strip()
    if not room_id or not user_unique_id:
        raise ValueError("room_id and user_unique_id are required")

    fields = (
        ("live_id", "1"),
        ("aid", "6383"),
        ("version_code", "180800"),
        ("webcast_sdk_version", "1.0.15"),
        ("room_id", room_id),
        ("sub_room_id", ""),
        ("sub_channel_id", ""),
        ("did_rule", "3"),
        ("user_unique_id", user_unique_id),
        ("device_platform", "web"),
        ("device_type", ""),
        ("ac", ""),
        ("identity", "audience"),
    )
    canonical = ",".join(f"{key}={value}" for key, value in fields)
    return hashlib.md5(canonical.encode("utf-8")).hexdigest()


def sign_stub_with_values(
    x_ms_stub: str,
    counter: int,
    random_flag: bool,
    payload_random: int,
    key_random: int,
) -> str:
    """Create a deterministic signature, matching the upstream signer test API."""
    try:
        stub_bytes = bytes.fromhex(x_ms_stub)
    except ValueError as exc:
        raise ValueError("X-MS-STUB must be a 32-character MD5 hex string") from exc
    if len(stub_bytes) != 16 or len(x_ms_stub) != 32:
        raise ValueError("X-MS-STUB must be a 32-character MD5 hex string")
    if not 0 <= counter <= 255:
        raise ValueError("counter must fit in one byte")
    for name, value in (("payload_random", payload_random), ("key_random", key_random)):
        if not 0 <= value < 255:
            raise ValueError(f"{name} must be in range 0..254")

    stub_digest = hashlib.md5(stub_bytes).digest()
    payload = bytearray((
        counter & 0x3F,
        0,
        1,  # Environment code
        14,  # User behavior code
        _EMPTY_BODY_DIGEST[14],
        _EMPTY_BODY_DIGEST[15],
        stub_digest[14],
        stub_digest[15],
        payload_random,
        0,
    ))
    for value in payload[:-1]:
        payload[-1] ^= value

    encrypted = _rc4(bytes((key_random,)), payload)
    flags = (1 << 6) | ((1 << 4) if random_flag else 0)
    raw = bytes((flags, key_random)) + encrypted
    return base64.b64encode(raw).decode("ascii").translate(_BASE64_TRANSLATION).rstrip("=")


class WebSocketSignatureGenerator:
    """Session-scoped signature sequence, compatible with douyinLive v2.3.1."""

    def __init__(self, random_byte: Callable[[], int] | None = None):
        self._random_byte = random_byte or (lambda: secrets.randbelow(255))
        self._counter = 0
        self._lock = threading.Lock()

    def sign(self, room_id: str, user_unique_id: str) -> str:
        stub = websocket_signature_stub(room_id, user_unique_id)
        with self._lock:
            flag_random = self._next_byte(allow_255=True)
            self._counter = (self._counter + 1) & 0xFF
            payload_random = self._next_byte()
            key_random = self._next_byte()
            return sign_stub_with_values(
                stub,
                self._counter & 0x3F,
                bool(flag_random & 1),
                payload_random,
                key_random,
            )

    def _next_byte(self, *, allow_255: bool = False) -> int:
        value = self._random_byte()
        if not 0 <= value <= 255 or (value == 255 and not allow_255):
            raise ValueError("random_byte must return 0..254 (or 0..255 for flag byte)")
        return value


def _rc4(key: bytes, data: bytes | bytearray) -> bytes:
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) & 0xFF
        state[i], state[j] = state[j], state[i]

    output = bytearray(len(data))
    i = j = 0
    for offset, value in enumerate(data):
        i = (i + 1) & 0xFF
        j = (j + state[i]) & 0xFF
        state[i], state[j] = state[j], state[i]
        output[offset] = value ^ state[(state[i] + state[j]) & 0xFF]
    return bytes(output)
