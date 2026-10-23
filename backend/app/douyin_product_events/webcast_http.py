from __future__ import annotations

import base64
import hashlib
import time


_ENCODING_S3 = "ckdp1h4ZKsUB80/Mfvw36XIgR25+WQAlEi7NLboqYTOPuzmFjJnryx9HVGDaStCe"
_ENCODING_S4 = "Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe"
_STANDARD_BASE64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def sm3_digest(data: bytes) -> bytes:
    try:
        return hashlib.new("sm3", data).digest()
    except ValueError as exc:
        raise RuntimeError("This Python/OpenSSL build does not provide SM3") from exc


def a_bogus(query: str, user_agent: str, *, now_ms: int | None = None) -> str:
    """Generate the HTTP a_bogus value; fixed-clock output matches streamget."""
    if not query or not user_agent:
        raise ValueError("query and user_agent are required")

    start = int(time.time() * 1000) if now_ms is None else now_ms
    end = start + 100
    suffix = b"cus"
    url_hash = sm3_digest(sm3_digest(query.encode() + suffix))
    customer_hash = sm3_digest(sm3_digest(suffix))
    ua_encrypted = _rc4(user_agent.encode(), bytes((0, 1, 14)))
    ua_hash = sm3_digest(_encode(ua_encrypted, _ENCODING_S3).encode())
    window_env = b"1920|1080|1920|1040|0|30|0|0|1872|92|1920|1040|1857|92|1|24|Win32"

    start_bytes = _u32(start)
    end_bytes = _u32(end)
    page_id = _u32(110624)
    args = _u32(0) + b"\x00\x01" + _u32(14)
    indexed: dict[int, int] = {
        8: 3,
        10: end,
        15: 0,
        16: start,
        18: 44,
        19: 0,
        20: start_bytes[0], 21: start_bytes[1], 22: start_bytes[2], 23: start_bytes[3],
        24: (start >> 32) & 0xFF, 25: (start >> 40) & 0xFF,
        26: args[0], 27: args[1], 28: args[2], 29: args[3],
        30: 0, 31: 1, 32: 0, 33: 0,
        34: args[6], 35: args[7], 36: args[8], 37: args[9],
        38: url_hash[21], 39: url_hash[22],
        40: customer_hash[21], 41: customer_hash[22],
        42: ua_hash[23], 43: ua_hash[24],
        44: end_bytes[0], 45: end_bytes[1], 46: end_bytes[2], 47: end_bytes[3],
        48: 3, 49: (end >> 32) & 0xFF, 50: (end >> 40) & 0xFF,
        51: 110624,
        52: page_id[0], 53: page_id[1], 54: page_id[2], 55: page_id[3],
        56: 6383, 57: 6383 & 0xFF, 58: (6383 >> 8) & 0xFF,
        59: (6383 >> 16) & 0xFF, 60: (6383 >> 24) & 0xFF,
        64: len(window_env), 65: len(window_env) & 0xFF, 66: (len(window_env) >> 8) & 0xFF,
        69: 0, 70: 0, 71: 0,
    }
    checksum = 0
    for index in (
        18, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33,
        35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50,
        52, 53, 54, 55, 57, 58, 59, 60, 65, 66, 70, 71,
    ):
        checksum ^= indexed[index]
    bb = bytes(indexed[index] for index in (
        18, 20, 52, 26, 30, 34, 58, 38, 40, 53, 42, 21, 27, 54, 55, 31,
        35, 57, 39, 41, 43, 22, 28, 32, 60, 36, 23, 29, 33, 37, 44, 45,
        59, 46, 47, 48, 49, 50, 24, 25, 65, 66, 70, 71,
    )) + window_env + bytes((checksum & 0xFF,))
    encrypted = _rc4(bb, b"y")
    random_prefix = b"".join((
        _mix_random(int(0.123456789 * 10000), (3, 45)),
        _mix_random(int(0.987654321 * 10000), (1, 0)),
        _mix_random(int(0.555555555 * 10000), (1, 5)),
    ))
    return _encode(random_prefix + encrypted, _ENCODING_S4) + "="


def _u32(value: int) -> bytes:
    return (value & 0xFFFFFFFF).to_bytes(4, "big")


def _mix_random(value: int, option: tuple[int, int]) -> bytes:
    low = value & 0xFF
    high = (value >> 8) & 0xFF
    return bytes((
        (low & 0xAA) | (option[0] & 0x55),
        (low & 0x55) | (option[0] & 0xAA),
        (high & 0xAA) | (option[1] & 0x55),
        (high & 0x55) | (option[1] & 0xAA),
    ))


def _encode(data: bytes, alphabet: str) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return encoded.translate(str.maketrans(_STANDARD_BASE64, alphabet)).rstrip("=")


def _rc4(data: bytes, key: bytes) -> bytes:
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) & 0xFF
        state[i], state[j] = state[j], state[i]
    output = bytearray(len(data))
    i = j = 0
    for index, value in enumerate(data):
        i = (i + 1) & 0xFF
        j = (j + state[i]) & 0xFF
        state[i], state[j] = state[j], state[i]
        output[index] = value ^ state[(state[i] + state[j]) & 0xFF]
    return bytes(output)
