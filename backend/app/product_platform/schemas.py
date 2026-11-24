from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..core.domain import Model


class MobileRequest(Model):
    mobile: str = Field(pattern=r"^1[2-9]\d{9}$")


class LoginRequest(MobileRequest):
    sms_code: str = Field(pattern=r"^\d{4,8}$")


class SearchRequest(Model):
    keyword: str = Field(max_length=200)
    monitor_id: str | None = Field(default=None, min_length=1, max_length=100)
    asset_id: str | None = Field(default=None, min_length=1, max_length=100)
    start: int = Field(default=0, ge=0, le=1_000_000)
    sort_type: Literal["time", "todaySales", "sales", "7Sales", "30Sales"] = "time"
    sort_order: Literal["asc", "desc"] = "desc"

    @field_validator("keyword")
    @classmethod
    def strip_keyword(cls, value: str) -> str:
        return value.strip()


class AddToShowcaseRequest(Model):
    platform_product_id: str = Field(min_length=1, max_length=100)
    monitor_id: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("platform_product_id")
    @classmethod
    def non_blank_product_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("商品 ID 不能为空")
        return value.strip()


class ScopeRequest(Model):
    main_ip_id: str = Field(min_length=1, max_length=100)


class XingdongliTokenRequest(Model):
    member_token: str = Field(min_length=1, max_length=4096)

    @field_validator("member_token")
    @classmethod
    def validate_token(cls, value: str) -> str:
        token = value.strip()
        if not token or any(not 33 <= ord(char) <= 126 for char in token):
            raise ValueError("请填写有效的 member-token")
        return token


class RoomPlatformBinding(Model):
    platform: Literal["xingdou", "daduoduo", "xingdongli"]
    ip_id: str = Field(default="", max_length=128)

    @field_validator("ip_id")
    @classmethod
    def strip_ip_id(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_ip_id(self):
        if self.platform == "daduoduo" and not self.ip_id:
            raise ValueError("达多多需要填写 ipId")
        if self.platform == "xingdongli" and not (self.ip_id.isascii() and self.ip_id.isdecimal()
                                                   and 1 <= len(self.ip_id) <= 20):
            raise ValueError("请选择星动力签约达人")
        if self.platform == "xingdou" and self.ip_id:
            raise ValueError("星斗直播间绑定只需要选择平台")
        return self


class RoomPlatformRequest(Model):
    binding: RoomPlatformBinding | None
