from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..core.domain import Model


class MobileRequest(Model):
    mobile: str = Field(pattern=r"^1[2-9]\d{9}$")


class LoginRequest(MobileRequest):
    sms_code: str = Field(pattern=r"^\d{4,8}$")


class SearchRequest(Model):
    keyword: str = Field(min_length=1, max_length=200)
    monitor_id: str | None = Field(default=None, min_length=1, max_length=100)
    asset_id: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("keyword")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("商品名称不能为空")
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


class RoomPlatformBinding(Model):
    platform: Literal["xingdou", "daduoduo"]
    ip_id: str = Field(default="", max_length=128)

    @field_validator("ip_id")
    @classmethod
    def strip_ip_id(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_ip_id(self):
        if self.platform == "daduoduo" and not self.ip_id:
            raise ValueError("达多多需要填写 ipId")
        if self.platform == "xingdou" and self.ip_id:
            raise ValueError("星斗直播间绑定只需要选择平台")
        return self


class RoomPlatformRequest(Model):
    binding: RoomPlatformBinding | None
