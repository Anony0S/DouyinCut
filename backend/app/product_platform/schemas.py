from pydantic import Field, field_validator

from ..core.domain import Model


class MobileRequest(Model):
    mobile: str = Field(pattern=r"^1[2-9]\d{9}$")


class LoginRequest(MobileRequest):
    sms_code: str = Field(pattern=r"^\d{4,8}$")


class SearchRequest(Model):
    keyword: str = Field(min_length=1, max_length=200)

    @field_validator("keyword")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("商品名称不能为空")
        return value.strip()


class ScopeRequest(Model):
    main_ip_id: str = Field(min_length=1, max_length=100)
