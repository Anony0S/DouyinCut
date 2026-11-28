from __future__ import annotations

import base64
import binascii
import ipaddress
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .providers import ProviderError


MAX_IMAGE_BYTES = 20 * 1024 * 1024
PRODUCT_IMAGE_HOSTS = {"p3-aio.ecombdimg.com", "p9-aio.ecombdimg.com"}


def image_extension(data: bytes | bytearray) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def _validate_product_image_url(url: str, allowed_hosts):
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname not in allowed_hosts
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError("商品图片地址无效")


def fetch_product_image(url: str, *, allowed_hosts=PRODUCT_IMAGE_HOSTS) -> tuple[bytes, str]:
    _validate_product_image_url(url, allowed_hosts)
    try:
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.HTTPError:
        raise ValueError("下载商品图片失败，请稍后重试") from None
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    data = response.content
    if not content_type.startswith("image/") or not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("商品图片格式无效或文件过大")
    return data, content_type


def fetch_xingdongli_product_image(url: str, *, allowed_hosts) -> tuple[bytes, str]:
    _validate_product_image_url(url, allowed_hosts)
    data = bytearray()
    try:
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", url, headers={"Accept": "image/jpeg, image/png, image/webp"}) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    if len(data) + len(chunk) > MAX_IMAGE_BYTES:
                        raise ValueError("星动力商品图片超过 20 MiB")
                    data.extend(chunk)
    except httpx.HTTPError:
        raise ValueError("下载星动力商品图片失败，请稍后重试") from None
    if not data:
        raise ValueError("星动力商品图片为空")
    content_type = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(image_extension(data))
    if content_type is None:
        raise ValueError("星动力商品图片不是可用的 PNG、JPEG 或 WebP 图片")
    return bytes(data), content_type


def _safe_result_url(url: str, base_url: str) -> bool:
    parsed, base = urlparse(url), urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    if parsed.port not in (None, 443):
        return False
    try:
        if ipaddress.ip_address(parsed.hostname).is_global is False:
            return False
    except ValueError:
        pass
    return parsed.hostname == base.hostname


def request_image(config: dict, api_key: str, source_path: str, prompt: str) -> tuple[bytes, str]:
    base_url = config["image_base_url"].rstrip("/")
    model = config["image_model"]
    url = f"{base_url}/images/edits"
    timeout = config["image_timeout_seconds"]
    headers = {"Authorization": f"Bearer {api_key}"}
    source = Path(source_path)
    mime = mimetypes.guess_type(source.name)[0] or "image/jpeg"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            with source.open("rb") as image_file:
                response = client.post(
                    url,
                    headers=headers,
                    data={"model": model, "prompt": prompt, "n": "1", "size": "auto"},
                    files={"image": (source.name, image_file, mime)},
                )
    except httpx.HTTPError:
        raise ProviderError("封面生图服务连接失败，请检查地址和网络") from None
    if response.status_code < 200 or response.status_code >= 300:
        request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
        request_suffix = f"，服务请求 ID：{request_id[:120]}" if request_id else ""
        if response.status_code == 404:
            path = urlparse(url).path
            raise ProviderError(
                f"封面生图服务请求失败（HTTP 404，接口路径 {path}）。请核对 Base URL 是否包含服务要求的 API 版本前缀，"
                f"并确认服务支持 /images/edits{request_suffix}"
            )
        raise ProviderError(f"封面生图服务请求失败（HTTP {response.status_code}）{request_suffix}")
    if len(response.content) > MAX_IMAGE_BYTES * 2:
        raise ProviderError("封面生图服务返回内容过大")
    try:
        body = response.json()
        image = body["data"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        raise ProviderError("封面生图服务未返回可识别的图片") from None
    if not isinstance(image, dict):
        raise ProviderError("封面生图服务未返回可识别的图片")
    encoded = image.get("b64_json")
    if isinstance(encoded, str):
        try:
            result = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ProviderError("封面生图服务返回了无效的图片数据") from None
        content_type = "image/png"
    else:
        result_url = image.get("url")
        if not isinstance(result_url, str) or not _safe_result_url(result_url, base_url):
            raise ProviderError("封面生图服务需返回 b64_json，或返回同域 HTTPS 图片地址")
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                result_response = client.get(result_url)
                result_response.raise_for_status()
        except httpx.HTTPError:
            raise ProviderError("无法下载封面生图服务返回的图片") from None
        result = result_response.content
        content_type = result_response.headers.get("content-type", "").split(";", 1)[0].lower()
    if not result or len(result) > MAX_IMAGE_BYTES or not content_type.startswith("image/"):
        raise ProviderError("封面生图服务返回的图片格式无效或文件过大")
    if image_extension(result) is None:
        raise ProviderError("封面生图服务返回内容不是有效的 PNG、JPEG 或 WebP 图片")
    return result, content_type
