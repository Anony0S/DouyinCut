import time

from pydantic import Field, field_validator

from ..core.domain import Model
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
