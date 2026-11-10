import time
import secrets
import re
from urllib.parse import urlparse

from pydantic import Field, field_validator

from ..core.domain import Model
from ..core.credentials import read_secret
from .providers import (CloudConfig, ExitInterrupted, ProviderError, ProviderHTTPError,
                        interruptible_network_call, model_api_key)


class ModelConnectionTestRequest(Model):
    config: CloudConfig
    api_key: str = Field(default="", max_length=20000, repr=False)

    @field_validator("config")
    @classmethod
    def require_text_connection(cls, config):
        try:
            config.require_text_connection()
        except ProviderError as exc:
            raise ValueError(str(exc)) from None
        return config


class OssConnectionTestRequest(Model):
    config: CloudConfig
    access_key_id: str = Field(default="", max_length=20000, repr=False)
    access_key_secret: str = Field(default="", max_length=20000, repr=False)


def check_oss_connection(request: OssConnectionTestRequest) -> dict:
    started = time.monotonic()
    result = {"ok": False}
    try:
        endpoint = urlparse(request.config.oss_endpoint)
        if (endpoint.scheme != "https" or not endpoint.hostname or
                not endpoint.hostname.endswith(".aliyuncs.com") or endpoint.username or endpoint.password or
                endpoint.query or endpoint.fragment or endpoint.path not in ("", "/")):
            raise ProviderError("OSS Endpoint 必须是阿里云 HTTPS 地址")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", request.config.oss_bucket):
            raise ProviderError("请填写有效的 OSS Bucket 名称（3–63 位小写字母、数字或连字符）")
        key_id = request.access_key_id.strip() or read_secret("oss_access_key_id")
        key_secret = request.access_key_secret.strip() or read_secret("oss_access_key_secret")
        if not key_id or not key_secret:
            raise ProviderError("请填写 OSS AccessKey ID 和 Secret")
        interruptible_network_call("oss_test", (request.config, secrets.token_hex(16), key_id, key_secret),
                                   deadline_seconds=30)
        result.update(ok=True, message="OSS 上传测试成功，当前配置具有写入权限")
    except ExitInterrupted:
        result.update(message="应用正在退出，测试已中断")
    except ProviderError as exc:
        details = exc.details or {}
        if details.get("stage") == "process_timeout":
            result.update(message="OSS 测试超时（30 秒），请检查网络和 Endpoint")
        elif details.get("stage") in ("oss_upload", "process_start", "process_exit"):
            hints = {"InvalidAccessKeyId": "AccessKey ID 无效或已停用",
                     "SignatureDoesNotMatch": "签名不匹配，请检查 AccessKey Secret",
                     "AccessDenied": "无写入权限，请检查 RAM 授权与 Bucket 策略",
                     "NoSuchBucket": "Bucket 不存在，请检查名称和地域",
                     "PermanentRedirect": "Endpoint 与 Bucket 地域不一致",
                     "RequestTimeTooSkewed": "本机时间偏差过大，请同步系统时间",
                     "RequestError": "网络连接失败，请检查网络、代理和 Endpoint"}
            parts = [str(details.get("reason", "请求失败"))]
            if details.get("http_status"):
                parts.append(f"HTTP {details['http_status']}")
            if details.get("oss_error_code"):
                parts.append(str(details["oss_error_code"]))
            hint = hints.get(details.get("oss_error_code"), hints.get(details.get("reason")))
            result.update(message="OSS 测试失败：" + (hint + "；" if hint else "") + "，".join(parts))
        else:
            result.update(message=str(exc))
    except Exception:
        result.update(message="OSS 测试无法完成，请检查本机凭据服务和运行环境")
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result


def check_model_connection(request: ModelConnectionTestRequest) -> dict:
    """Probe text generation once without saving settings or sending user media."""
    config = request.config
    started = time.monotonic()
    result = {"ok": False, "model": config.planner_model}
    try:
        key = request.api_key.strip() or model_api_key(config)
        # A one-off probe runs in a child process so desktop exit never waits for the model.
        # It keeps no operation record: an interrupted test is simply not replayed.
        response = interruptible_network_call(
            "model_test",
            ("POST", config.chat_url,
             {"model": config.planner_model,
              "messages": [{"role": "user", "content": "这是连接测试，请只回复 OK，不要解释。"}],
              "stream": False, "max_tokens": 128}, key),
            kwargs={"timeout_seconds": min(config.chat_timeout_seconds, 60), "operation": "model_test"},
        )
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            content = None
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("模型未返回有效文本，请检查模型 ID 与接口兼容性")
        result.update(ok=True, message="文本模型可用，已收到有效回复")
    except ExitInterrupted as exc:
        result.update(message=str(exc))
    except ProviderHTTPError as exc:
        hints = {401: "API Key 无效或已失效", 403: "无权访问该模型，请检查模型权限",
                 404: "模型或接口不存在，请检查 Base URL 和模型 ID",
                 429: "请求受限，请检查余额、配额或稍后重试"}
        hint = hints.get(exc.status_code, "云端拒绝或未能完成请求，请检查服务状态与模型配置")
        result.update(message=f"{hint}（HTTP {exc.status_code}）")
    except ProviderError as exc:
        # Provider messages are controlled locally; never return a cloud body or credential.
        result.update(message=str(exc).split("，悬停", 1)[0])
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result
