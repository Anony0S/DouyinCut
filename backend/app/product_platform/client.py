from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

import httpx


BASE_URL = "https://api.aixingdou.com/prod/qiongqi"
# This is the signing constant shipped in the platform's public web bundle.
SIGNING_SECRET = "EC7259DFCAD1715C23D400B7A88407A3"


class _HidePlatformQuery(logging.Filter):
    def filter(self, record):
        # HTTPX's INFO request log otherwise contains login codes and access tokens.
        if isinstance(record.args, tuple):
            record.args = tuple(
                value.copy_with(query=None) if isinstance(value, httpx.URL) and value.host == "api.aixingdou.com" else value
                for value in record.args
            )
        return True


logging.getLogger("httpx").addFilter(_HidePlatformQuery())


def sign_params(params: dict[str, Any]) -> str:
    # The web client's `!value && 0 != value` includes empty strings and false.
    def value_text(value):
        return str(value).lower() if isinstance(value, bool) else str(value)
    parts = [f"{key}={value_text(params[key])}" for key in sorted(params)
             if params[key] is not None]
    parts.append(f"secret={SIGNING_SECRET}")
    return hashlib.md5("&".join(parts).encode()).hexdigest().upper()


class ProductPlatformError(RuntimeError):
    def __init__(self, message: str, *, code: str | int | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


class ProductPlatformClient:
    def __init__(self, token: str | None = None, client: httpx.Client | None = None):
        self.token = token or ""
        self.client = client or httpx.Client(base_url=BASE_URL, timeout=15, follow_redirects=False, trust_env=False)
        self._owns_client = client is None

    def close(self):
        if self._owns_client:
            self.client.close()

    def _request(self, path: str, method: str = "GET", values: dict[str, Any] | None = None,
                 *, authenticated: bool = False) -> dict[str, Any]:
        values = dict(values or {})
        values["timestamp"] = int(time.time())
        values["sign_type"] = "md5"
        values["sign"] = sign_params(values)
        headers = {"member-token": self.token}
        try:
            if method == "POST":
                response = self.client.post(path, params={"access-token": self.token}, json=values, headers=headers)
            else:
                response = self.client.get(path, params={**values, "access-token": self.token}, headers=headers)
            response.raise_for_status()
            body = response.json()
        except httpx.RequestError:
            raise ProductPlatformError("星斗商品平台网络请求失败，请检查网络后重试", status=503) from None
        except httpx.HTTPStatusError as exc:
            if authenticated and exc.response.status_code == 401:
                raise ProductPlatformError("星斗商品平台登录已失效", code="PRODUCT_PLATFORM_AUTH_REQUIRED", status=401) from None
            raise ProductPlatformError("星斗商品平台请求失败", status=502) from None
        except ValueError:
            raise ProductPlatformError("星斗商品平台返回了无法识别的数据", status=502) from None
        if not isinstance(body, dict):
            raise ProductPlatformError("星斗商品平台返回了无法识别的数据", status=502)
        if body.get("success") is not True:
            code = body.get("code")
            message = str(body.get("message") or "星斗商品平台请求失败")
            if authenticated and (str(code) == "401" or any(text in message for text in ("未登录", "登录已失效", "登录过期", "重新登录"))):
                raise ProductPlatformError("星斗商品平台登录已失效", code="PRODUCT_PLATFORM_AUTH_REQUIRED", status=401)
            # An upstream message must never echo the submitted token/code/phone.
            for sensitive in (self.token, str(values.get("mobile") or ""), str(values.get("smsCode") or ""), values["sign"]):
                if sensitive:
                    message = message.replace(sensitive, "[已隐藏]")
            raise ProductPlatformError(message[:300], code=code, status=400)
        return body

    def send_code(self, mobile: str) -> dict[str, Any]:
        return self._request("/openapi/member/verification/code", values={"mobile": mobile})

    def login(self, mobile: str, sms_code: str) -> str:
        body = self._request("/openapi/member/mobile/login", values={"mobile": mobile, "smsCode": sms_code})
        token = body.get("data")
        if not isinstance(token, str) or not token:
            raise ProductPlatformError("星斗商品平台登录未返回有效会话", status=502)
        return token

    def member_info(self) -> dict[str, Any]:
        data = self._request("/xd/frontend/member/getMemberInfo", authenticated=True).get("data")
        if not isinstance(data, dict) or not data.get("id"):
            raise ProductPlatformError("星斗商品平台账号信息无效，请重试", status=502)
        return data

    def member_ips(self) -> list[dict[str, Any]]:
        data = self._request("/xd/frontend/contract/member/ip", authenticated=True).get("data")
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise ProductPlatformError("星斗商品平台查询范围数据无效，请重试", status=502)
        return data

    def search(self, keyword: str, main_ip_id: str) -> list[dict[str, Any]]:
        body = self._request("/xd/frontend/product/search", "POST", {
            "mainIpId": main_ip_id,
            "sortFieldType": 1,
            "sortType": 0,
            "keyword": keyword,
            "presellType": "",
            "hasLive": "",
            "brand_name": "",
            "start": 0,
            "length": 20,
        }, authenticated=True)
        data = body.get("data") or {}
        products = data.get("data") if isinstance(data, dict) else None
        # The platform returns an explicit null list for successful zero-match searches.
        if isinstance(data, dict) and "data" in data and products is None and data.get("total") in (0, "0"):
            return []
        if not isinstance(products, list) or any(not isinstance(item, dict) for item in products):
            raise ProductPlatformError("星斗商品平台搜索结果格式无效，请重试", status=502)
        return products

    def add_to_showcase(self, ip_id: str, platform_product_id: str, member_talent_id: str) -> None:
        try:
            member_talent_id_value = int(member_talent_id)
        except (TypeError, ValueError):
            raise ProductPlatformError("星斗商品平台达人关联信息无效，请重新登录后重试", status=502) from None
        body = self._request("/xd/frontend/product/talentStoreProductAdd", "POST", {
            "ipId": ip_id,
            "platformProductIdList": [platform_product_id],
            "memberTalentIdList": [member_talent_id_value],
        }, authenticated=True)
        data = body.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ProductPlatformError("星斗商品平台添加结果格式无效，请重试", status=502)
