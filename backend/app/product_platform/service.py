from __future__ import annotations

import time
from threading import RLock
from typing import Any

from ..core.credentials import read_secret, write_secret
from ..core.storage import Store
from .client import ProductPlatformClient, ProductPlatformError


TOKEN_KEY = "xingdou_product_platform_token"
CONFIG_KIND = "config"
CONFIG_ID = "product_platform"


class ProductPlatformAuthRequired(ProductPlatformError):
    def __init__(self, message: str = "星斗商品平台未登录"):
        super().__init__(message, code="PRODUCT_PLATFORM_AUTH_REQUIRED", status=401)


def _masked_mobile(value: str | None) -> str:
    if not value:
        return ""
    return f"{value[:3]}****{value[-4:]}" if len(value) >= 7 else "****"


class ProductPlatformService:
    def __init__(self, store: Store):
        self.store = store
        self._lock = RLock()
        self._sms_sent_at = float('-inf')

    @staticmethod
    def _write_token(token: str):
        try:
            write_secret(TOKEN_KEY, token)
        except Exception:
            raise ProductPlatformError("系统凭据存储不可用，无法保存星斗登录信息", status=503) from None

    def _config(self) -> dict[str, Any]:
        try:
            return self.store.get(CONFIG_KIND, CONFIG_ID)
        except KeyError:
            return {"id": CONFIG_ID, "logged_in": False}

    def _save_config(self, config: dict[str, Any]):
        try:
            self.store.next_revision(CONFIG_KIND, CONFIG_ID, config)
        except KeyError:
            self.store.put(CONFIG_KIND, config)

    def status(self) -> dict[str, Any]:
        config = self._config()
        token = read_secret(TOKEN_KEY)
        scopes = [{"id": item["id"], "name": item["name"]} for item in config.get("scopes", [])]
        return {
            "platform": "xingdou",
            "logged_in": bool(token and config.get("logged_in")),
            "mobile": config.get("mobile", ""),
            "member_name": config.get("member_name", ""),
            "member_id": config.get("member_id"),
            "main_ip_id": config.get("main_ip_id"),
            "main_ip_name": config.get("main_ip_name", ""),
            "scopes": scopes,
            "last_validated_at": config.get("last_validated_at"),
        }

    def send_code(self, mobile: str):
        with self._lock:
            remaining = 60 - (time.monotonic() - self._sms_sent_at)
            if remaining > 0:
                raise ProductPlatformError(f"请在 {int(remaining) + 1} 秒后重新获取验证码", status=429)
            client = ProductPlatformClient()
            try:
                client.send_code(mobile)
                self._sms_sent_at = time.monotonic()
            finally:
                client.close()
            return {"sent": True, "retry_after_seconds": 60, "message": "验证码发送成功"}

    def login(self, mobile: str, sms_code: str):
        with self._lock:
            client = ProductPlatformClient()
            try:
                token = client.login(mobile, sms_code)
                client.token = token
                member = client.member_info()
                ips = client.member_ips()
            finally:
                client.close()
            scopes = self._scopes(ips)
            ip = scopes[0] if scopes else {}
            config = {
                "id": CONFIG_ID, "logged_in": True,
                "mobile": _masked_mobile(str(member.get("mobile") or mobile)),
                "member_name": str(member.get("name") or ""),
                "member_id": str(member["id"]),
                "main_ip_id": ip.get("id", ""), "main_ip_name": ip.get("name", ""),
                "scopes": scopes, "last_validated_at": time.time(),
            }
            previous_token = read_secret(TOKEN_KEY) or ""
            self._write_token(token)
            try:
                self._save_config(config)
            except Exception:
                self._write_token(previous_token)
                raise ProductPlatformError("星斗账号信息保存失败，请检查工作目录后重试", status=503) from None
            return self.status()

    @staticmethod
    def _scopes(ips):
        scopes = {}
        for ip in ips:
            ip_id = str(ip.get("cooperationIpId") or "")
            if ip_id:
                scopes[ip_id] = {"id": ip_id, "name": str(ip.get("cooperationIpName") or ip_id)}
        return list(scopes.values())

    def select_scope(self, main_ip_id: str):
        with self._lock:
            config = self._config()
            if not read_secret(TOKEN_KEY) or not config.get("logged_in"):
                raise ProductPlatformAuthRequired()
            scope = next((item for item in config.get("scopes", []) if item["id"] == main_ip_id), None)
            if scope is None:
                raise ProductPlatformError("请选择当前账号关联的查询范围", status=422)
            self._save_config({**config, "main_ip_id": scope["id"], "main_ip_name": scope["name"]})
            return self.status()

    def logout(self):
        with self._lock:
            self._write_token("")
            self._save_config({"id": CONFIG_ID, "logged_in": False})
            return self.status()

    def search(self, keyword: str):
        with self._lock:
            config = self._config()
            token = read_secret(TOKEN_KEY)
            main_ip_id = str(config.get("main_ip_id") or "")
            if not token or not config.get("logged_in"):
                raise ProductPlatformAuthRequired()
            if not main_ip_id:
                raise ProductPlatformError("当前账号没有关联的商品查询范围，请先在星斗平台确认合作权限", status=409)
            client = ProductPlatformClient(token)
            try:
                items = client.search(keyword, main_ip_id)
            except ProductPlatformError as exc:
                if exc.code == "PRODUCT_PLATFORM_AUTH_REQUIRED":
                    # Mark invalid before credential cleanup so a storage failure cannot leave a valid-looking state.
                    self._save_config({"id": CONFIG_ID, "logged_in": False})
                    try:
                        self._write_token("")
                    except ProductPlatformError:
                        pass  # The invalid state already prevents reuse; still send the user to login.
                    raise ProductPlatformAuthRequired() from None
                raise
            finally:
                client.close()
            self._save_config({**config, "last_validated_at": time.time()})
            return {
                "product": self._product(items[0]) if items else None,
                "scope_name": config.get("main_ip_name", ""),
            }

    def add_to_showcase(self, platform_product_id: str):
        with self._lock:
            config = self._config()
            token = read_secret(TOKEN_KEY)
            if not token or not config.get("logged_in"):
                raise ProductPlatformAuthRequired()
            ip_id = str(config.get("main_ip_id") or "")
            if not ip_id:
                raise ProductPlatformError("当前账号没有关联的商品查询范围，请先在星斗平台确认合作权限", status=409)
            try:
                client = ProductPlatformClient(token)
                try:
                    relation = next((item for item in client.member_ips()
                                     if str(item.get("cooperationIpId") or "") == ip_id), None)
                    member_talent_id = str(relation.get("memberTalentId") or "") if relation else ""
                    if not member_talent_id:
                        raise ProductPlatformError("当前合作范围缺少添加橱窗所需的达人关联信息", status=409)
                    client.add_to_showcase(ip_id, platform_product_id, member_talent_id)
                finally:
                    client.close()
            except ProductPlatformError as exc:
                if exc.code == "PRODUCT_PLATFORM_AUTH_REQUIRED":
                    self._save_config({"id": CONFIG_ID, "logged_in": False})
                    try:
                        self._write_token("")
                    except ProductPlatformError:
                        pass
                    raise ProductPlatformAuthRequired() from None
                raise
            self._save_config({**config, "last_validated_at": time.time()})
            return {"added": True}

    @staticmethod
    def _product(item):
        # Only the fields shown by the product card cross the local API boundary.
        fields = {
            "id": "id", "platform_product_id": "platformProductId", "title": "title",
            "price": "price", "stock": "productStock", "cover": "cover", "shop_name": "shopName",
            "real_time_sales": "realTimeSaleRange", "month_sales": "day30SaleRange",
            "total_sales": "preciseSaleRange", "commission_ratio": "activityCosRatio",
            "has_live": "hasLive", "has_ai_live": "hasAiLive",
            "promotion_start": "promotionStartTime", "promotion_end": "promotionEndTime",
        }
        return {key: item.get(source) for key, source in fields.items()}
