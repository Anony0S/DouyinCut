import hashlib
import http.client
import json
import multiprocessing
import re
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from ..core.credentials import read_secret
from ..core.domain import Model, Transcript
from ..media.processing import extract_audio, probe_stream_timing


TRANSCRIPT_GROUP_MAX_US = 8_000_000
TRANSCRIPT_COMMA_SPLIT_MIN_US = 3_500_000


class CloudConfig(Model):
    region: Literal["cn"] = "cn"
    # Empty fields mean unconfigured; saving one card must not require the others.
    base_url: str = Field(default="", max_length=500)
    planner_model: str = Field(default="", max_length=100)
    asr_base_url: str = Field(default="", max_length=500)
    asr_model: str = Field(default="", max_length=100,
                           pattern=r"^(?:[A-Za-z0-9][A-Za-z0-9._-]*)?$")
    oss_endpoint: str = Field(default="", max_length=300)
    oss_bucket: str = Field(default="", max_length=100)
    chat_timeout_seconds: int = Field(default=300, ge=30, le=900)
    asr_timeout_seconds: int = Field(default=300, ge=30, le=900)

    @field_validator("planner_model", "asr_model", mode="before")
    @classmethod
    def normalize_model_id(cls, value):
        if not isinstance(value, str):
            return value
        value = value.strip()
        return value

    @field_validator("base_url", "asr_base_url")
    @classmethod
    def valid_base_url(cls, value):
        value = value.strip()
        if not value:
            return value
        value = value.rstrip("/")
        parsed = urlparse(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or any(c.isspace() for c in value)):
            raise ValueError("模型 Base URL 必须是无用户名、密码或查询参数的 HTTPS 地址")
        if parsed.path.endswith("/chat/completions"):
            raise ValueError("Base URL 不要包含 /chat/completions")
        if parsed.path.endswith("/api/v1/services/aigc/multimodal-generation/generation"):
            raise ValueError("Base URL 不要包含语音识别接口路径")
        return value

    def require_text_connection(self):
        if not self.base_url or not self.planner_model:
            raise ProviderError("请在文本剪辑模型卡片填写 Base URL 和模型 ID")

    def require_asr_connection(self):
        if not self.asr_base_url or not self.asr_model:
            raise ProviderError("请在语音识别模型卡片填写 Base URL 和模型 ID")

    @property
    def asr_url(self):
        return f"{self.asr_base_url}/api/v1/services/aigc/multimodal-generation/generation"

    @property
    def chat_url(self):
        return f"{self.base_url}/chat/completions"


def model_api_key(config):
    key = read_secret("llm_api_key")
    if key:
        config.require_text_connection()
        return key
    raise ProviderError("请在文本剪辑模型卡片配置 API Key")


def asr_api_key(config):
    key = read_secret("asr_api_key")
    if key:
        config.require_asr_connection()
        return key
    raise ProviderError("请在语音识别模型卡片配置 API Key")


class ProviderError(RuntimeError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details


class ProviderHTTPError(ProviderError):
    def __init__(self, status_code: int, details: dict | None = None):
        self.status_code = status_code
        super().__init__(f"云端请求失败（HTTP {status_code}），请检查凭据、模型和服务区域", details)


class ExitInterrupted(RuntimeError):
    """A cloud operation stopped because this desktop session is explicitly exiting."""


_SHUTDOWN = threading.Event()


def begin_shutdown():
    """Let in-flight one-off cloud calls abort; background jobs use their worker checkpoint instead."""
    _SHUTDOWN.set()


def clear_shutdown():
    _SHUTDOWN.clear()


def _network_child(pipe, kind, args, kwargs):
    try:
        if kind in {"asr", "planner", "model_test"}:
            result = cloud_json(*args, **kwargs)
        elif kind == "oss_test":
            result = _test_oss_request(*args, **kwargs)
        else:
            result = _upload_audio_request(*args, **kwargs)
        pipe.send(("ok", result))
    except ProviderHTTPError as exc:
        pipe.send(("http", exc.status_code, exc.details))
    except ProviderError as exc:
        pipe.send(("provider", str(exc), exc.details))
    except BaseException as exc:
        if kind in {"upload", "oss_test"}:
            # Only expose bounded identifiers; exception text may contain signed URLs or credentials.
            code = getattr(exc, "code", None)
            status = getattr(exc, "status", None)
            details = {"stage": "oss_upload", "error_source": "transport",
                       "reason": type(exc).__name__, "response_body": None}
            if isinstance(status, int) and 100 <= status <= 599:
                details["http_status"] = status
                details["error_source"] = "http_response"
            if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_]{1,80}", code):
                details["oss_error_code"] = code
            pipe.send(("provider", "OSS 上传请求失败", details))
        else:
            pipe.send(("error", type(exc).__name__, str(exc)))
    finally:
        pipe.close()


def interruptible_network_call(kind, args, *, store=None, operation_id=None, cache_key=None, checkpoint=None,
                               kwargs=None, deadline_seconds=None):
    """Run one network call in a child process so an exit can stop it without waiting.

    Background jobs pass the store so the reply is accepted inside the exit gate.
    One-off calls such as the settings model test pass neither and keep no replay state.
    """
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_network_child, args=(send, kind, args, kwargs or {}),
                              name=f"douyincut-cloud-{kind}")
    try:
        process.start()
    except BaseException as exc:
        if store:
            store.fail_cloud_operation(operation_id)
        receive.close()
        send.close()
        process.close()
        raise ProviderError("无法启动可中断的云端请求进程",
                            {"stage": "process_start", "reason": type(exc).__name__,
                             "response_body": None}) from None
    send.close()
    deadline = time.monotonic() + deadline_seconds if deadline_seconds else None
    try:
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                if store:
                    store.fail_cloud_operation(operation_id)
                raise ProviderError(f"云端请求超时（{deadline_seconds} 秒）",
                                    {"stage": "process_timeout", "reason": "TimeoutError",
                                     "timeout_seconds": deadline_seconds, "response_body": None})
            if receive.poll(0.05):
                try:
                    message = receive.recv()
                except EOFError:
                    process.join(2)
                    if store:
                        store.fail_cloud_operation(operation_id)
                    raise ProviderError("云端请求进程未返回结果",
                                        {"stage": "process_exit", "reason": f"exit code: {process.exitcode}",
                                         "response_body": None}) from None
                process.join(2)
                if message[0] == "ok":
                    body = None
                    if kind == "asr":
                        body = {"status": "response_received", "response": cached_audio_response(message[1], args[3])}
                    elif kind == "upload":
                        config, object_id = args[1], args[2]
                        body = {"status": "succeeded", "object_id": object_id,
                                "endpoint": config.oss_endpoint, "bucket": config.oss_bucket}
                    if store and not store.complete_cloud_operation(operation_id, cache_key, body):
                        raise ExitInterrupted("云端响应未被采用：会话正在退出，或任务已由新的执行接管")
                    return message[1]
                if store:
                    store.fail_cloud_operation(operation_id)
                if message[0] == "http":
                    raise ProviderHTTPError(message[1], message[2])
                if message[0] == "provider":
                    raise ProviderError(message[1], message[2])
                raise ProviderError(f"云端子进程失败（{message[1]}）")
            if checkpoint is None and _SHUTDOWN.is_set():
                process.terminate()
                process.join(2)
                raise ExitInterrupted("桌面会话正在退出，已中断本次网络请求")
            if checkpoint:
                try:
                    checkpoint()
                except BaseException:
                    if process.is_alive():
                        process.terminate()
                    process.join(2)
                    if process.is_alive():
                        process.kill()
                        process.join(2)
                    if store:
                        store.fail_cloud_operation(operation_id)
                    raise
            if not process.is_alive():
                if store:
                    store.fail_cloud_operation(operation_id)
                raise ProviderError("云端请求进程提前退出",
                                    {"stage": "process_exit", "reason": f"exit code: {process.exitcode}",
                                     "response_body": None})
    finally:
        receive.close()
        if process.is_alive():
            process.terminate()
            process.join(2)
        if process.is_alive():
            process.kill()
            process.join(2)
        process.close()


def redact_cloud_text(value: str, api_key: str | None = None) -> str:
    if api_key:
        value = value.replace(api_key, "[已隐藏密钥]")
    value = re.sub(r'https?://[^\s<>"\']+', lambda match:
                   match[0].split("?", 1)[0] + "?[已隐藏查询参数]" if "?" in match[0] else match[0], value)
    value = re.sub(r'(?i)\bBearer\s+[^\s"\'<>]+', 'Bearer [已隐藏密钥]', value)
    value = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{8,}|LTAI[A-Za-z0-9]{8,})\b', '[已隐藏密钥]', value)
    value = re.sub(r'(?i)(["\']?(?:api[_-]?key|access[_-]?key[_-]?(?:id|secret)|'
                   r'authorization|signature)["\']?\s*[:=]\s*)["\']?[^\s,"\'<>}]+["\']?',
                   r'\1"[已隐藏密钥]"', value)
    return value


def cloud_error_details(url, payload, stage, *, api_key=None, status=None, body=None,
                        request_id=None, content_type=None, reason=None,
                        request_method=None, request_headers=None, request_body=None):
    detail = {
        "error_source": "response_validation" if stage == "response_validation" else
                        "http_response" if stage in {"http_error", "json_decode"} else "transport",
        "stage": stage, "endpoint": redact_cloud_text(url),
        "model": payload.get("model") if isinstance(payload, dict) else None,
        "http_status": status, "request_id": request_id, "content_type": content_type,
        "reason": redact_cloud_text(reason, api_key) if reason else None,
        "response_body": redact_cloud_text(body, api_key) if body is not None else None,
    }
    if request_method:
        detail["request_method"] = request_method
    if request_headers:
        detail["request_headers"] = request_headers
    if request_body is not None:
        detail["request_body"] = request_body
    return detail


def parsed_response_details(response, url, model, api_key):
    output = response.get("output") if isinstance(response, dict) else None
    request_id = response.get("request_id") if isinstance(response, dict) else None
    if not request_id and isinstance(output, dict):
        request_id = output.get("request_id")
    return cloud_error_details(url, {"model": model}, "response_validation", api_key=api_key,
                               status=200, request_id=request_id, content_type="application/json",
                               body=json.dumps(response, ensure_ascii=False, indent=2))


def cached_audio_response(value, api_key):
    """Redact values before JSON serialization so transcript quoting stays valid."""
    if isinstance(value, dict):
        private = {"authorization", "apikey", "accesskeyid", "accesskeysecret", "signature"}
        return {name: "[已隐藏密钥]" if re.sub(r"[_-]", "", name).lower() in private
                else cached_audio_response(item, api_key) for name, item in value.items()}
    if isinstance(value, list):
        return [cached_audio_response(item, api_key) for item in value]
    return redact_cloud_text(value, api_key) if isinstance(value, str) else value


def sanitized_request_payload(value, api_key, path=()):
    """Redact request secrets while preserving the ASR input_audio URL for diagnosis."""
    if isinstance(value, dict):
        return {name: (item if path and path[-1] == "input_audio" and name == "data"
                       else sanitized_request_payload(item, api_key, path + (name,)))
                for name, item in value.items()}
    if isinstance(value, list):
        return [sanitized_request_payload(item, api_key, path) for item in value]
    return redact_cloud_text(value, api_key) if isinstance(value, str) else value


# These responses explicitly reject the request; timeouts and server errors remain uncertain.
ASR_REJECTED_HTTP_STATUSES = {400, 401, 403, 404, 405, 413, 415, 422, 429}


def cloud_json(method: str, url: str, payload=None, api_key: str | None = None,
               sse: bool | None = None, *, timeout_seconds: int = 90,
               operation: str | None = None):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if sse is not None:
        headers["X-DashScope-SSE"] = "enable" if sse else "disable"
    request = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                                     headers=headers, method=method)
    started = time.monotonic()
    image_count = sum(1 for message in payload.get("messages", [])
                      if isinstance(message.get("content"), list)
                      for item in message["content"] if isinstance(item, dict)
                      and item.get("type") == "image_url") if isinstance(payload, dict) else 0

    def diagnostic(stage, **kwargs):
        safe_headers = {name: "[已隐藏密钥]" if name.lower() == "authorization" else value
                        for name, value in headers.items()}
        safe_body = None
        if operation == "asr" and payload is not None:
            safe_body = json.dumps(sanitized_request_payload(payload, api_key),
                                   ensure_ascii=False, indent=2)
        detail = cloud_error_details(url, payload, stage, api_key=api_key,
                                     request_method=method, request_headers=safe_headers,
                                     request_body=safe_body, **kwargs)
        detail.update(operation=operation, timeout_seconds=timeout_seconds,
                      elapsed_seconds=round(time.monotonic() - started, 2),
                      request_bytes=len(request.data or b""), image_count=image_count)
        return detail
    raw = None
    status, response_headers = None, {}
    try:
        if api_key:
            # Do not forward a credential to a redirected endpoint.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None
            response = urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout_seconds)
        else:
            response = urllib.request.urlopen(request, timeout=timeout_seconds)
        with response:
            status = getattr(response, "status", 200)
            response_headers = getattr(response, "headers", {})
            raw = response.read()
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            details = diagnostic("json_decode", status=status,
                                         body=raw.decode("utf-8", errors="replace"),
                                         request_id=response_headers.get("X-Request-Id"),
                                         content_type=response_headers.get("Content-Type"), reason=str(exc))
            raise ProviderError("云端响应不是有效 JSON，悬停错误图标查看完整返回内容", details) from None
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except http.client.IncompleteRead as read_error:
            body = read_error.partial.decode("utf-8", errors="replace")
        except (OSError, AttributeError):
            body = None
        details = diagnostic("http_error", status=exc.code,
                                     body=body, request_id=exc.headers.get("X-Request-Id") if exc.headers else None,
                                     content_type=exc.headers.get("Content-Type") if exc.headers else None)
        raise ProviderHTTPError(exc.code, details) from None
    except http.client.IncompleteRead as exc:
        details = diagnostic("incomplete_response",
                                     status=status, body=exc.partial.decode("utf-8", errors="replace"),
                                     request_id=response_headers.get("X-Request-Id"),
                                     content_type=response_headers.get("Content-Type"), reason=str(exc))
        raise ProviderError("云端响应传输中断，悬停错误图标查看已收到的内容", details) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        timeout = isinstance(reason, TimeoutError)
        disconnected = isinstance(reason, http.client.RemoteDisconnected)
        details = diagnostic("remote_disconnected" if disconnected else
                             "timeout" if timeout else "connection_error", status=status,
                                     content_type=response_headers.get("Content-Type"),
                                     reason=f"{type(reason).__name__}: {reason}")
        details["exception_type"] = type(reason).__name__
        message = ("对端在返回 HTTP 响应前断开连接" if disconnected else
                   f"云端连接超时（{timeout_seconds} 秒）" if timeout else "云端连接失败")
        raise ProviderError(message + "，悬停错误图标查看详情", details) from None


def required_secret(name: str):
    value = read_secret(name)
    if not value:
        raise ProviderError(f"尚未配置 {name}")
    return value


def _test_oss_request(config: CloudConfig, object_id: str, access_key_id: str, access_key_secret: str):
    import oss2
    bucket = oss2.Bucket(oss2.Auth(access_key_id, access_key_secret), config.oss_endpoint, config.oss_bucket,
                         connect_timeout=10)
    bucket.put_object(f"douyincut/audio/test-{object_id}.txt", b"DouyinCut OSS test")
    return True


def _upload_audio_request(audio: Path, config: CloudConfig, object_id: str, access_key_id: str,
                         access_key_secret: str) -> str:
    import oss2
    auth = oss2.Auth(access_key_id, access_key_secret)
    bucket = oss2.Bucket(auth, config.oss_endpoint, config.oss_bucket)
    key = f"douyincut/audio/{object_id}.wav"
    bucket.put_object_from_file(key, str(audio))
    return bucket.sign_url("GET", key, 24 * 3600)


def _signed_audio_url(config: CloudConfig, object_id: str, access_key_id: str, access_key_secret: str) -> str:
    import oss2
    auth = oss2.Auth(access_key_id, access_key_secret)
    bucket = oss2.Bucket(auth, config.oss_endpoint, config.oss_bucket)
    return bucket.sign_url("GET", f"douyincut/audio/{object_id}.wav", 24 * 3600)


def upload_audio(audio: Path, config: CloudConfig, object_id: str, *, operation=None) -> str:
    if not config.oss_endpoint or not config.oss_bucket:
        raise ProviderError("Qwen 语音识别需要 OSS 音频地址，请先配置 OSS")
    endpoint = urlparse(config.oss_endpoint)
    if endpoint.scheme != "https" or not endpoint.hostname or not endpoint.hostname.endswith(".aliyuncs.com") or endpoint.username or endpoint.password:
        raise ProviderError("OSS endpoint 必须是阿里云 HTTPS 地址")
    access_key_id = required_secret("oss_access_key_id")
    access_key_secret = required_secret("oss_access_key_secret")
    try:
        # Overwrite deterministic keys so a previous empty/corrupt upload cannot
        # be reused after local extraction has been fixed.
        if operation is None:
            return _upload_audio_request(audio, config, object_id, access_key_id, access_key_secret)
        store, job_id, checkpoint = operation
        upload_cache_key = f"oss-upload:{config.oss_endpoint}:{config.oss_bucket}:{object_id}"
        uploaded = store.cached(upload_cache_key)
        if (uploaded and uploaded.get("status") == "succeeded" and uploaded.get("object_id") == object_id
                and uploaded.get("endpoint") == config.oss_endpoint and uploaded.get("bucket") == config.oss_bucket):
            return _signed_audio_url(config, object_id, access_key_id, access_key_secret)
        operation_id = store.begin_cloud_operation(job_id, "oss_upload", config.model_dump())
        return interruptible_network_call("upload", (audio, config, object_id, access_key_id, access_key_secret),
                                            store=store, operation_id=operation_id, cache_key=upload_cache_key,
                                            checkpoint=checkpoint, deadline_seconds=300)
    except ImportError:
        raise ProviderError("尚未安装 OSS 客户端依赖") from None
    except ExitInterrupted:
        raise
    except ProviderError as exc:
        raise ProviderError("OSS 上传失败，请检查任务错误详情", exc.details) from None
    except Exception as exc:
        raise ProviderError("OSS 上传失败，请检查任务错误详情",
                            {"stage": "oss_upload", "reason": type(exc).__name__,
                             "response_body": None}) from None


ASR_CHUNK_US = 120_000_000
ASR_MIN_CHUNK_US = 1_000_000


def qwen_audio_payload(model: str, audio_url: str) -> dict:
    """Build the native Qwen Audio request exactly as documented by Bailian."""
    return {
        "model": model,
        "input": {
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "input_audio",
                    "input_audio": {"data": audio_url},
                }],
            }],
        },
        "parameters": {"format": "wav", "sample_rate": "16000"},
    }


def audio_payload(response) -> dict:
    if isinstance(response, dict):
        # Native responses can repeat the same result at the root and under output.
        # Choose one representation, never concatenate both.
        output = response.get("output")
        for payload in (output, response):
            if isinstance(payload, dict) and any(
                    name in payload for name in ("sentence", "sentences", "text", "choices")):
                return payload
    raise ProviderError("Qwen 语音模型返回格式无效，未找到转写文本；请检查模型与多模态接口支持")


def audio_sentences(payload) -> list[dict]:
    value = payload.get("sentences", payload.get("sentence"))
    if value is None:
        return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list) or not all(
            isinstance(item, dict) and isinstance(item.get("text"), str) for item in value):
        raise ProviderError("Qwen 语音模型返回的句子格式无效")
    if any(item.get("sentence_end", True) is not True for item in value):
        raise ProviderError("Qwen 语音模型返回了未完成的句子，未保存为完整转写")
    return value


def audio_text(response) -> str:
    """Accept native ASR sentence/text results and chat-compatible content."""
    payload = audio_payload(response)
    sentences = audio_sentences(payload)
    if isinstance(payload.get("text"), str):
        return payload["text"].strip()
    if sentences:
        return "\n".join(item["text"] for item in sentences).strip()
    try:
        message = payload["choices"][0]["message"]
        content = message["content"]
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list) and content and all(
                isinstance(item, dict) and isinstance(item.get("text"), str) for item in content):
            return "\n".join(item["text"] for item in content).strip()
    except (KeyError, IndexError, TypeError):
        pass
    raise ProviderError("Qwen 语音模型返回格式无效，未找到转写文本；请检查模型与多模态接口支持")


def aggregate_transcript(segment: Transcript) -> bool:
    """A paragraph-level ASR timestamp cannot locate individual sentences inside it."""
    duration = segment.end_us - segment.start_us
    stops = len(re.findall(r"[。！？!?]", segment.text))
    return duration > 45_000_000 or (duration > 20_000_000 and (stops > 1 or len(segment.text) > 120))


def word_timed_sentences(sentence, asset_id, start_us, end_us):
    words = sentence.get("words")
    if not words:
        return None
    if not isinstance(words, list):
        raise ProviderError("ASR 词级时间戳格式无效")
    tokens = []
    for word in words:
        if not isinstance(word, dict):
            raise ProviderError("ASR 词级时间戳格式无效")
        begin, end = word.get("begin_time"), word.get("end_time")
        text, punctuation = word.get("text"), word.get("punctuation", "")
        if (type(begin) is not int or type(end) is not int or not 0 <= begin < end
                or end * 1000 > end_us - start_us or not isinstance(text, str)
                or not isinstance(punctuation, str) or (tokens and begin < tokens[-1][1])):
            raise ProviderError("ASR 词级时间戳无效、重叠或超出音频段范围")
        if word.get("fixed", True) is not True:
            raise ProviderError("ASR 返回未完成的词级结果")
        if text.strip():
            tokens.append((begin, end, text + (punctuation if not text.endswith(punctuation) else "")))
    # Never replace a full paragraph with an incomplete words list.
    normalize = lambda value: re.sub(r"[\W_]", "", value, flags=re.UNICODE).casefold()
    if not tokens or normalize("".join(t[2] for t in tokens)) != normalize(sentence["text"]):
        raise ProviderError("ASR 词级结果与完整转写不一致，无法精确拆分口播")
    result, group = [], []

    def flush():
        if group:
            result.append(Transcript(source_asset_id=asset_id, start_us=start_us + group[0][0] * 1000,
                                     end_us=start_us + group[-1][1] * 1000,
                                     text="".join(t[2] for t in group).strip(), timing="sentence"))
            group.clear()

    for token in tokens:
        if group and token[0] - group[-1][1] >= 600:
            flush()
        if group and (token[1] - group[0][0]) * 1000 > TRANSCRIPT_GROUP_MAX_US:
            flush()
        group.append(token)
        if re.search(r"[。！？!?；;.]$", token[2].strip()):
            flush()
        elif ((token[1] - group[0][0]) * 1000 >= TRANSCRIPT_COMMA_SPLIT_MIN_US
              and re.search(r"[，,、：:]$", token[2].strip())):
            flush()
    flush()
    return result


def audio_segments(response, asset_id: str, start_us: int, end_us: int) -> list[Transcript]:
    payload = audio_payload(response)
    sentences = audio_sentences(payload)
    timed = []
    all_timed = bool(sentences)
    for sentence in sentences:
        text = sentence["text"].strip()
        precise = word_timed_sentences(sentence, asset_id, start_us, end_us)
        if precise is not None:
            timed.extend(precise)
            continue
        if len(text) > 5000:
            raise ProviderError("Qwen 语音返回文本过长，请检查模型输出")
        if "begin_time" not in sentence and "end_time" not in sentence:
            all_timed = False
            continue
        begin, end = sentence.get("begin_time"), sentence.get("end_time")
        # Provider times are milliseconds relative to the uploaded WAV, not the video.
        if (type(begin) is not int or type(end) is not int
                or not 0 <= begin < end or end * 1000 > end_us - start_us):
            raise ProviderError("Qwen 语音模型返回无效或超出音频段范围的句级时间戳")
        if text:
            timed.append(Transcript(source_asset_id=asset_id, start_us=start_us + begin * 1000,
                                    end_us=start_us + end * 1000, text=text, timing="sentence"))
    if all_timed:
        unique = {(t.start_us, t.end_us, t.text): t for t in timed}
        return sorted(unique.values(), key=lambda t: (t.start_us, t.end_us))
    text = audio_text(response)
    if len(text) > 5000:
        raise ProviderError("Qwen 语音返回文本过长，请检查模型输出")
    return [Transcript(source_asset_id=asset_id, start_us=start_us, end_us=end_us,
                       text=text, timing="audio_chunk")] if text else []


def transcribe(asset, config, settings, store, workdir, checkpoint, progress=None, *, require_sentence_timing=False,
               refresh_scope: str | None = None, job_id: str | None = None):
    key = asr_api_key(config)
    audio_start_us = (probe_stream_timing(Path(asset.path), settings)["audio_start_us"]
                      if Path(asset.path).is_file() else getattr(asset, "audio_start_us", 0))
    endpoint_hash = hashlib.sha256(config.asr_url.encode()).hexdigest()
    segments = []
    total = (asset.duration_us + ASR_CHUNK_US - 1) // ASR_CHUNK_US
    for index, start in enumerate(range(0, asset.duration_us, ASR_CHUNK_US)):
        checkpoint()
        end = min(start + ASR_CHUNK_US, asset.duration_us)
        if end - start < ASR_MIN_CHUNK_US:
            # Ignore ffprobe rounding noise at the end of a file instead of
            # producing and uploading a header-only WAV.
            if progress:
                progress((index + 1) / total)
            continue
        cache_key = f"qwen-audio-v1:{asset.sha256}:{endpoint_hash}:{config.asr_model}:{start}:{end}"
        base_key = cache_key
        if refresh_scope:
            # Explicit refresh is scoped to one task: retries still reuse its paid results.
            cache_key += f":refresh:{refresh_scope}"
        state = store.cached(cache_key)
        previous = store.cached(base_key) if refresh_scope else None
        if previous and previous.get("status") == "submitting" and previous.get("refresh_scope") != refresh_scope:
            raise ProviderError("之前的 ASR 请求结果不确定，请先核查云端记录，不能通过重新识别绕过防重复计费保护",
                                previous.get("error_detail"))
        if state and state.get("status") in {"succeeded", "response_received"}:
            chunk_segments = audio_segments(state["response"], asset.id, start, end)
            if state.get("status") == "response_received":
                state = {"status": "succeeded", "response": state["response"],
                         "segments": [t.model_dump(exclude={"source_asset_id"}) for t in chunk_segments],
                         "text": "\n".join(t.text for t in chunk_segments)}
                store.cache(cache_key, state)
        else:
            if state and state.get("status") == "submitting":
                raise ProviderError(f"Qwen 语音片段 {index + 1} 的请求结果不确定，停止自动重提以避免重复计费；请核查该模型请求记录",
                                    state.get("error_detail"))
            audio = workdir / f"audio-{start}-{end}.wav"
            if audio_start_us:
                extract_audio(asset, audio, settings, checkpoint, start_us=start, end_us=end,
                              audio_start_us=audio_start_us)
            else:
                extract_audio(asset, audio, settings, checkpoint, start_us=start, end_us=end)
            operation = (store, job_id, checkpoint) if job_id else None
            file_url = upload_audio(audio, config, f"{asset.sha256}-qwen-audio-v1-{start}-{end}",
                                    operation=operation)
            checkpoint()
            operation_id = None
            if job_id:
                operation_id = store.begin_cloud_operation(job_id, "asr", config.model_dump(), cache_key)
                if refresh_scope:
                    store.cache(base_key, {"status": "submitting", "exit_operation_id": operation_id,
                                           "refresh_scope": refresh_scope})
            else:
                submitting = {"status": "submitting", "started_at": time.time()}
                if refresh_scope:
                    submitting["refresh_scope"] = refresh_scope
                    store.cache(base_key, submitting)
                store.cache(cache_key, submitting)
            try:
                args = ("POST", config.asr_url, qwen_audio_payload(config.asr_model, file_url), key)
                if operation_id:
                    response = interruptible_network_call(
                        "asr", args, store=store, operation_id=operation_id, cache_key=cache_key,
                        checkpoint=checkpoint,
                        kwargs={"sse": False, "operation": "asr",
                                "timeout_seconds": config.asr_timeout_seconds})
                else:
                    response = cloud_json(*args, sse=False, operation="asr",
                                          timeout_seconds=config.asr_timeout_seconds)
            except ProviderHTTPError as exc:
                if exc.status_code in ASR_REJECTED_HTTP_STATUSES:
                    rejection = {"status": "rejected", "http_status": exc.status_code}
                    if exc.details:
                        rejection["error_detail"] = exc.details
                    store.cache(cache_key, rejection)
                    if refresh_scope:
                        store.cache(base_key, rejection)
                    hint = "请检查语音识别模型卡片中的 API Key" if exc.status_code == 401 else "请检查语音识别连接、模型权限或配额"
                    raise ProviderError(f"Qwen 语音请求被拒绝（HTTP {exc.status_code}），{hint}，修正后可重试", exc.details) from None
                store.cache(cache_key, {**store.cached(cache_key), "error_detail": exc.details})
                if refresh_scope:
                    store.cache(base_key, store.cached(cache_key))
                raise
            except ProviderError as exc:
                store.cache(cache_key, {**store.cached(cache_key), "error_detail": exc.details})
                if refresh_scope:
                    store.cache(base_key, store.cached(cache_key))
                raise
            try:
                chunk_segments = audio_segments(response, asset.id, start, end)
            except ProviderError as exc:
                # The response arrived, so a user-triggered retry can request it again.
                details = parsed_response_details(response, config.asr_url, config.asr_model, key)
                store.cache(cache_key, {"status": "invalid_response", "error_detail": details})
                if refresh_scope:
                    store.cache(base_key, store.cached(cache_key))
                raise ProviderError(str(exc), details) from None
            completed = {"status": "succeeded", "text": "\n".join(t.text for t in chunk_segments),
                         "segments": [t.model_dump(exclude={"source_asset_id"}) for t in chunk_segments]}
            completed["response"] = cached_audio_response(response, key)
            if any(t.timing != "sentence" for t in chunk_segments):
                completed["error_detail"] = parsed_response_details(response, config.asr_url, config.asr_model, key)
            store.cache(cache_key, completed)
            if refresh_scope:
                store.cache(base_key, completed)
        if require_sentence_timing and any(t.timing != "sentence" for t in chunk_segments):
            # Keep a received text-only result cached so retries cannot silently charge again.
            raise ProviderError("ASR 仅返回整段文本，缺少句级时间戳，无法精确粗剪；请检查长音频接口的多句返回格式",
                                store.cached(cache_key).get("error_detail"))
        if require_sentence_timing and any(aggregate_transcript(t) for t in chunk_segments):
            details = (parsed_response_details(state["response"], config.asr_url, config.asr_model, key)
                       if state and "response" in state else store.cached(cache_key).get("error_detail"))
            raise ProviderError("ASR 返回的是长段口播的整体时间戳，缺少可裁剪的短句 / 词级时间；"
                                "请检查接口是否返回完整 words 或短句时间戳", details)
        segments.extend(chunk_segments)
        if progress:
            progress((index + 1) / total)
    return segments


