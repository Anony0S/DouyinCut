"""Member session and product queries for the Xingdongli mini program."""

import hashlib
import json
import time
from threading import RLock
from urllib.parse import urlsplit

import httpx

from ..core.credentials import read_secret, write_secret
from .client import ProductPlatformError
from .service import _masked_mobile


ORIGIN = "https://www.xingdongliqiepian.com"
PRODUCT_IMAGE_HOSTS = ("lf3-cm.ecombdstatic.com", "lf26-cm.ecombdstatic.com")
TOKEN_KEY = "xingdongli_member_token"
CONFIG_ID = "xingdongli_product_platform"
AUTH_REQUIRED = "XINGDONGLI_AUTH_REQUIRED"
PAGE_SIZE = 20
MINI_PROGRAM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
        "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) "
        "UnifiedPCWindowsWechat(0xf254200d) XWEB/26134"
    ),
    "Referer": "https://servicewechat.com/wxb8c1a5a08a42b3dd/16/page-frame.html",
    "Content-Type": "application/json",
    "Accept": "*/*",
    "xweb_xhr": "1",
}


def auth_required():
    return ProductPlatformError("星动力登录已失效或尚未配置，请填写有效的 member-token",
                                code=AUTH_REQUIRED, status=401)


def _request(client, path, payload=None):
    try:
        # The mini program authenticates with member-token on each request.
        client.cookies.clear()
        response = client.get(path) if payload is None else client.post(path, json=payload)
        if response.status_code == 401:
            raise auth_required()
        if response.status_code == 403:
            raise ProductPlatformError("星动力拒绝访问，请检查签约达人权限", status=403)
        if response.status_code == 429:
            raise ProductPlatformError("星动力请求过于频繁，请稍后重试", status=429)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError):
        raise ProductPlatformError("星动力请求失败，请检查网络或稍后重试", status=502) from None
    if not isinstance(body, dict):
        raise ProductPlatformError("星动力返回了无效数据", status=502)
    if body.get("success") is True and "data" in body:
        return body["data"]
    if body.get("code") in (401, "401") or body.get("message") in ("未登录", "请先登录", "登录已过期", "登录已失效"):
        raise auth_required()
    raise ProductPlatformError("星动力未返回有效结果，请检查账号和签约达人", status=502)


def _identifier(value):
    return str(value).strip() if type(value) in (int, str) else ""


def _scopes(value):
    if not isinstance(value, list):
        raise ProductPlatformError("星动力签约达人列表格式无效", status=502)
    scopes = {}
    for item in value:
        identifier = _identifier(item.get("id")) if isinstance(item, dict) else ""
        if not identifier.isascii() or not identifier.isdecimal() or not 1 <= len(identifier) <= 20:
            raise ProductPlatformError("星动力签约达人记录缺少有效的 ipId", status=502)
        scopes[identifier] = {"id": identifier, "name": str(item.get("nickname") or identifier)}
    return list(scopes.values())


def _product(item):
    product_id = _identifier(item.get("productId")) if isinstance(item, dict) else ""
    if not product_id or not isinstance(item.get("productName"), str) or not item["productName"].strip():
        raise ProductPlatformError("星动力商品记录缺少商品 ID 或名称", status=502)
    cover = item.get("productImg")
    try:
        url = urlsplit(cover) if isinstance(cover, str) else None
        if (url is None or url.scheme != "https" or url.hostname not in PRODUCT_IMAGE_HOSTS
                or url.username or url.password or url.port not in (None, 443)):
            cover = None
    except ValueError:
        cover = None
    create_time = item.get("createTime")
    promotion_url = item.get("promotionUrl")
    return {
        "id": _identifier(item.get("id")) or product_id,
        "platform_product_id": product_id, "title": item["productName"],
        "price": item.get("productPrice"), "shop_name": item.get("shopName"),
        "stock": item.get("productStock"), "cover": cover,
        "listing_time": (create_time.strip() or None) if isinstance(create_time, str) else None,
        "promotion_url": (promotion_url.strip() or None) if isinstance(promotion_url, str) else None,
        "real_time_sales": item.get("todaySale"), "yesterday_sales": item.get("lastSale"),
        "month_sales": item.get("last30Sale"), "commission_ratio": item.get("commissionRate"),
    }


class XingdongliService:
    def __init__(self, store):
        self.store = store
        self.lock = RLock()

    @staticmethod
    def _client(token):
        return httpx.Client(base_url=ORIGIN, headers={**MINI_PROGRAM_HEADERS, "member-token": token},
                            timeout=15, follow_redirects=False, trust_env=False)

    @staticmethod
    def _write_token(token):
        try:
            write_secret(TOKEN_KEY, token)
        except Exception:
            raise ProductPlatformError("系统凭据存储不可用，无法保存星动力登录信息", status=503) from None

    def _config(self):
        try:
            return self.store.get("config", CONFIG_ID)
        except KeyError:
            return {}

    def _save_config(self, details):
        with self.store.connection() as conn:
            conn.execute("INSERT OR REPLACE INTO objects VALUES ('config', ?, 1, ?, ?)",
                         (CONFIG_ID, json.dumps({"id": CONFIG_ID, **details}, ensure_ascii=False), time.time()))

    def _invalidate_session(self):
        self._save_config({})
        try:
            self._write_token("")
        except ProductPlatformError:
            pass

    def status(self):
        with self.lock:
            config = self._config()
            token = read_secret(TOKEN_KEY)
            logged_in = bool(token and config.get("token_hash") == hashlib.sha256(token.encode()).hexdigest())
            return {
                "platform": "xingdongli", "logged_in": logged_in,
                "member_name": config.get("member_name", "") if logged_in else "",
                "mobile": config.get("mobile", "") if logged_in else "",
                "scopes": config.get("scopes", []) if logged_in else [],
                "last_validated_at": config.get("last_validated_at") if logged_in else None,
            }

    def connect(self, replacement=None):
        with self.lock:
            previous = read_secret(TOKEN_KEY)
            token = replacement if replacement is not None else previous
            if not token:
                raise auth_required()
            try:
                with self._client(token) as client:
                    member = _request(client, "/frontend/member/get")
                    member_id = _identifier(member.get("id")) if isinstance(member, dict) else ""
                    if not member_id:
                        raise ProductPlatformError("星动力会员信息格式无效", status=502)
                    scopes = _scopes(_request(client, "/frontend/contract/contractIp"))
            except ProductPlatformError as exc:
                if replacement is None and exc.code == AUTH_REQUIRED:
                    self._invalidate_session()
                raise
            if replacement is not None:
                self._write_token(token)
            try:
                self._save_config({
                    "member_id": member_id, "member_name": str(member.get("nickname") or ""),
                    "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                    "mobile": _masked_mobile(str(member.get("mobile") or "")),
                    "scopes": scopes, "last_validated_at": time.time(),
                })
            except Exception:
                if replacement is not None:
                    self._write_token(previous or "")
                raise ProductPlatformError("星动力账号信息保存失败，请检查工作目录后重试", status=503) from None
            return self.status()

    def logout(self):
        with self.lock:
            self._write_token("")
            self._save_config({})
            return self.status()

    def scope(self, ip_id):
        status = self.status()
        if not status["logged_in"]:
            raise auth_required()
        scope = next((item for item in status["scopes"] if item["id"] == ip_id), None)
        if scope is None:
            raise ProductPlatformError("请选择星动力当前账号的签约达人", status=409)
        return scope

    def search(self, ip_id, keyword, start, sort_type="time", sort_order="desc"):
        with self.lock:
            scope = self.scope(ip_id)
            token = read_secret(TOKEN_KEY)
            if not token:
                raise auth_required()
            try:
                with self._client(token) as client:
                    page = _request(client, "/frontend/product/list-by-ip", {
                        "ipId": int(ip_id), "start": start, "length": PAGE_SIZE,
                        "searchProduct": keyword, "sortType": sort_type, "sortOrder": sort_order,
                        "onlyHasMaterial": False,
                    })
            except ProductPlatformError as exc:
                if exc.code == AUTH_REQUIRED:
                    self._invalidate_session()
                raise
        if (not isinstance(page, dict) or type(page.get("total")) is not int or page["total"] < 0
                or not isinstance(page.get("data"), list) or len(page["data"]) > PAGE_SIZE):
            raise ProductPlatformError("星动力商品分页格式无效", status=502)
        products = [_product(item) for item in page["data"]]
        return {
            "product": products[0] if products else None, "products": products, "total": page["total"],
            "start": start, "length": PAGE_SIZE, "scope_name": scope["name"], "can_add_to_showcase": False,
        }
