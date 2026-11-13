from __future__ import annotations

import contextlib
import mimetypes
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from google import genai
from google.genai import types

from clio.ai.base import AIResponse, StructuredOutput, TokenUsage, is_truncated_response, normalize_response_reason
from clio.config import ProviderConfig, ProxyConfig
from clio.i18n import t
from clio.prompts import TRUNCATED_RETRY_PROMPT
from clio.ratelimit import make_rate_limiter, wait_for_rate_limit
from clio.utils import mask_if_looks_like_key, retry_after_seconds, with_retry

try:
    from google.genai import errors as _genai_errors

    _HAS_GENAI_ERRORS = True
except ImportError:
    _HAS_GENAI_ERRORS = False


_BLOCKING_FINISH_REASONS = {
    "SAFETY",
    "RECITATION",
    "BLOCKLIST",
    "PROHIBITED_CONTENT",
    "SPII",
    "IMAGE_SAFETY",
}


_MEDIA_RESOLUTION_MAP = {
    "low": types.MediaResolution.MEDIA_RESOLUTION_LOW,
    "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
    "high": types.MediaResolution.MEDIA_RESOLUTION_HIGH,
}


def _normalize_gemini_reason(reason: object) -> str | None:
    candidate = getattr(reason, "name", None) or getattr(reason, "value", None)
    if candidate is None and isinstance(reason, str):
        candidate = reason
    if not isinstance(candidate, str):
        return None
    return normalize_response_reason(candidate)


def _extract_finish_reason(response) -> str | None:
    candidates = getattr(response, "candidates", None)
    if not isinstance(candidates, (list, tuple)) or not candidates:
        return None
    reason = getattr(candidates[0], "finish_reason", None)
    return _normalize_gemini_reason(reason)


def _extract_block_reason(response) -> str | None:
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is None:
        return None
    reason = getattr(feedback, "block_reason", None)
    normalized = _normalize_gemini_reason(reason)
    if normalized is None or normalized == "BLOCK_REASON_UNSPECIFIED":
        return None
    return normalized


def _response_from(response) -> AIResponse:
    text = response.text or ""
    finish_reason = _extract_finish_reason(response)
    block_reason = _extract_block_reason(response)
    if block_reason is None and not text and finish_reason in _BLOCKING_FINISH_REASONS:
        block_reason = finish_reason
    return AIResponse(
        text="" if block_reason is not None else text,
        token_usage=_extract_usage(response),
        finish_reason=finish_reason,
        block_reason=block_reason,
    )


def _usage_count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _extract_usage(response) -> TokenUsage | None:
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return None
    candidates_tokens = _usage_count(getattr(meta, "candidates_token_count", None))
    thoughts_tokens = _usage_count(getattr(meta, "thoughts_token_count", None))
    return TokenUsage(
        prompt_tokens=_usage_count(getattr(meta, "prompt_token_count", None)),
        completion_tokens=candidates_tokens + thoughts_tokens,
        total_tokens=_usage_count(getattr(meta, "total_token_count", None)),
    )


def _sum_usage(*usages: TokenUsage | None) -> TokenUsage | None:
    present = [usage for usage in usages if usage is not None]
    if not present:
        return None
    return TokenUsage(
        prompt_tokens=sum(usage.prompt_tokens for usage in present),
        completion_tokens=sum(usage.completion_tokens for usage in present),
        total_tokens=sum(usage.total_tokens for usage in present),
    )


class GeminiProvider:
    provider_id = "gemini"

    def __init__(self, cfg: ProviderConfig, proxy: ProxyConfig):
        if not cfg.api_key:
            env_name = mask_if_looks_like_key(cfg.api_key_env) or t("ai.env_not_set")
            raise ValueError(t("ai.gemini_missing_key", {"provider": cfg.name, "env": env_name}))
        http_options_kwargs: dict = {"timeout": max(1, int(float(cfg.timeout_sec) * 1000))}
        if proxy.enabled and proxy.url:
            http_options_kwargs["client_args"] = {"transport": httpx.HTTPTransport(proxy=proxy.url)}
            http_options_kwargs["async_client_args"] = {"transport": httpx.AsyncHTTPTransport(proxy=proxy.url)}
        http_options = types.HttpOptions(**http_options_kwargs)
        self._client = genai.Client(api_key=cfg.api_key, http_options=http_options)
        self._rl = make_rate_limiter(cfg.requests_per_minute)
        self._poll_interval = cfg.poll_interval_sec
        self._retry_attempts = max(1, cfg.retry_attempts + 1)
        self._max_tokens = int(cfg.max_tokens)
        self._video_upload_max_mb = int(cfg.video_upload_max_mb)
        self._generation_config = (
            types.GenerateContentConfig(max_output_tokens=self._max_tokens) if self._max_tokens > 0 else None
        )

    def _config_for(
        self,
        temperature: float | None,
        system: str | None = None,
        structured_output: StructuredOutput | None = None,
        media_resolution: str | None = None,
        max_output_tokens: int | None = None,
    ):
        """Build a per-call config when per-call options are requested.

        The cached `_generation_config` is shared across concurrent calls, so it
        must never be mutated in place.
        """
        if (
            temperature is None
            and not system
            and structured_output is None
            and media_resolution is None
            and max_output_tokens is None
        ):
            return self._generation_config
        kwargs: dict = {}
        if temperature is not None:
            kwargs["temperature"] = float(temperature)
        output_tokens = self._max_tokens if max_output_tokens is None else max_output_tokens
        if output_tokens > 0:
            kwargs["max_output_tokens"] = output_tokens
        if system:
            kwargs["system_instruction"] = system
        if structured_output is not None:
            kwargs["response_mime_type"] = "application/json"
            if structured_output.schema is not None:
                kwargs["response_json_schema"] = structured_output.schema
        if media_resolution is not None:
            try:
                kwargs["media_resolution"] = _MEDIA_RESOLUTION_MAP[media_resolution]
            except KeyError as exc:
                available = ", ".join(_MEDIA_RESOLUTION_MAP)
                raise ValueError(
                    t("ai.media_resolution_invalid", {"value": media_resolution, "available": available})
                ) from exc
        return types.GenerateContentConfig(**kwargs)

    def _is_retryable(self, exc: BaseException) -> bool:
        """判断异常是否应该重试。"""
        if isinstance(exc, (OSError, httpx.TransportError)):
            return True
        if _HAS_GENAI_ERRORS:
            if isinstance(exc, _genai_errors.ServerError):
                return True
            if isinstance(exc, _genai_errors.ClientError):
                code = getattr(exc, "code", None)
                if code == 429:
                    return True
                status = getattr(exc, "status", "") or ""
                return "RATE_LIMIT" in status.upper() or "RESOURCE_EXHAUSTED" in status.upper()
        return False

    def _retryable_types(self):
        """返回 with_retry 需要的异常类型元组。"""
        types_list: list[type[Exception]] = [OSError, httpx.TransportError]
        if _HAS_GENAI_ERRORS:
            types_list.append(_genai_errors.ClientError)
            types_list.append(_genai_errors.ServerError)
        return tuple(types_list)

    def _call_with_retry(self, fn, what, model, cancel_event: threading.Event | None = None):
        """统一的重试入口：将非重试 ClientError 转成 RuntimeError 避免误重试。"""

        def _wrapped():
            try:
                return fn()
            except InterruptedError:
                raise
            except BaseException as e:
                if self._is_retryable(e):
                    raise
                raise RuntimeError(t("ai.gemini_non_retryable", {"error": e})) from e

        return with_retry(
            _wrapped,
            attempts=self._retry_attempts,
            base_delay=2.0,
            max_delay=60.0,
            retry_on=self._retryable_types(),
            what=f"Gemini {what}",
            # 用 should_retry 进一步过滤：只有 _is_retryable 为 True 才重试
            should_retry=lambda e: self._is_retryable(e),
            delay_for=retry_after_seconds,
            cancel_event=cancel_event,
        )

    def _get_file_with_retry(
        self,
        name: str,
        *,
        deadline: float,
        timeout: float,
        cancel_event: threading.Event | None,
    ):
        for attempt in range(self._retry_attempts):
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError(t("ai.video_cancelled"))
            if time.monotonic() > deadline:
                raise TimeoutError(t("ai.video_timeout", {"timeout": timeout, "name": name}))

            try:
                return self._client.files.get(name=name)
            except BaseException as exc:
                if not self._is_retryable(exc) or attempt + 1 >= self._retry_attempts:
                    raise

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(t("ai.video_timeout", {"timeout": timeout, "name": name})) from exc
                delay = min(2.0 * (2**attempt), remaining)
                print(
                    t(
                        "ai.gemini_retry_status",
                        {
                            "error_type": type(exc).__name__,
                            "detail": str(exc)[:80],
                            "delay": f"{delay:.1f}",
                            "attempt": attempt + 2,
                            "total": self._retry_attempts,
                        },
                    )
                )
                if cancel_event is not None:
                    if cancel_event.wait(delay):
                        raise RuntimeError(t("ai.video_cancelled")) from exc
                else:
                    time.sleep(delay)

        raise AssertionError("Gemini file polling retry loop exhausted unexpectedly")

    def _wait_for_file(
        self,
        uploaded,
        timeout: float = 300,
        on_progress: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ):
        deadline = time.monotonic() + timeout
        while uploaded.state == types.FileState.PROCESSING:
            # Check for cancellation
            if cancel_event and cancel_event.is_set():
                raise RuntimeError(t("ai.video_cancelled"))
            if time.monotonic() > deadline:
                raise TimeoutError(t("ai.video_timeout", {"timeout": timeout, "name": uploaded.name}))
            waited = int(time.monotonic() - (deadline - timeout))
            if on_progress:
                on_progress(t("ai.waiting_gemini_progress", {"waited": waited}))
            print(t("ai.video_processing_console", {"waited": waited}))
            if cancel_event is not None:
                if cancel_event.wait(self._poll_interval):
                    raise RuntimeError(t("ai.video_cancelled"))
            else:
                time.sleep(self._poll_interval)
            uploaded = self._get_file_with_retry(
                uploaded.name,
                deadline=deadline,
                timeout=timeout,
                cancel_event=cancel_event,
            )
        if uploaded.state == types.FileState.FAILED:
            raise RuntimeError(t("ai.video_processing_failed", {"name": uploaded.name}))
        return uploaded

    def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):  # SDK client cleanup is best effort
                self._client.close()

    def generate_text(
        self,
        prompt: str,
        model: str,
        cancel_event: threading.Event | None = None,
        *,
        temperature: float | None = None,
        system: str | None = None,
        structured_output: StructuredOutput | None = None,
    ) -> AIResponse:
        def _do() -> AIResponse:
            wait_for_rate_limit(self._rl, cancel_event, what=self.provider_id)
            config = self._config_for(temperature, system, structured_output)
            if config is None:
                response = self._client.models.generate_content(model=model, contents=prompt)
            else:
                response = self._client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config,
                )
            return _response_from(response)

        return self._call_with_retry(_do, model, model, cancel_event)

    def analyze_images(
        self,
        image_paths: list[str],
        prompt: str,
        model: str,
        cancel_event: threading.Event | None = None,
        *,
        temperature: float | None = None,
        system: str | None = None,
        structured_output: StructuredOutput | None = None,
    ) -> AIResponse:
        def _do() -> AIResponse:
            wait_for_rate_limit(self._rl, cancel_event, what=self.provider_id)
            contents: list[types.Part | str] = [
                types.Part.from_bytes(data=Path(path).read_bytes(), mime_type="image/png") for path in image_paths
            ]
            contents.append(prompt)
            config = self._config_for(temperature, system, structured_output)
            if config is None:
                response = self._client.models.generate_content(model=model, contents=contents)
            else:
                response = self._client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
            return _response_from(response)

        return self._call_with_retry(_do, model, model, cancel_event)

    def analyze_video(
        self,
        video_path: str,
        prompt: str,
        model: str,
        progress_callback: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
        *,
        temperature: float | None = None,
        system: str | None = None,
        structured_output: StructuredOutput | None = None,
        video_start_sec: float | None = None,
        video_end_sec: float | None = None,
        video_fps: float | None = None,
        media_resolution: str | None = None,
    ) -> AIResponse:
        if (video_start_sec is None) != (video_end_sec is None):
            raise ValueError("video_start_sec and video_end_sec must be provided together")
        if video_fps is not None and video_start_sec is None:
            raise ValueError("video_fps requires video_start_sec and video_end_sec")
        if video_start_sec is not None and video_end_sec is not None and video_end_sec < video_start_sec:
            raise ValueError("video_end_sec must be greater than or equal to video_start_sec")
        uploaded = None
        try:
            if progress_callback:
                progress_callback(t("ai.progress_uploading"))
            # 用二进制流避免 google-genai 2.8.0 的 bug：
            # 非 ASCII 文件名会作为 HTTP header 值发送，导致
            # UnicodeEncodeError: 'ascii' codec can't encode character
            p = Path(video_path)
            size = p.stat().st_size
            if size > self._video_upload_max_mb * 1024 * 1024:
                raise ValueError(
                    t(
                        "ai.file_too_large",
                        {
                            "size": f"{size / 1024 / 1024:.0f}",
                            "limit": str(self._video_upload_max_mb),
                        },
                    )
                )
            mime_type, _ = mimetypes.guess_type(str(p))

            def _upload():
                if cancel_event and cancel_event.is_set():
                    raise RuntimeError(t("ai.video_analysis_cancelled"))
                wait_for_rate_limit(self._rl, cancel_event, what=self.provider_id)
                with open(video_path, "rb") as f:
                    return self._client.files.upload(
                        file=f,
                        config=types.UploadFileConfig(mime_type=mime_type or "video/mp4"),
                    )

            uploaded = with_retry(
                _upload,
                attempts=self._retry_attempts,
                base_delay=2.0,
                max_delay=60.0,
                retry_on=self._retryable_types(),
                what=t("ai.retry_what_upload"),
                should_retry=self._is_retryable,
                delay_for=retry_after_seconds,
                cancel_event=cancel_event,
            )
            if progress_callback:
                progress_callback(t("ai.progress_waiting"))
            # Pass cancel_event to _wait_for_file
            uploaded = self._wait_for_file(uploaded, on_progress=progress_callback, cancel_event=cancel_event)

            if progress_callback:
                progress_callback(t("ai.progress_analyzing"))

            contents: list = [uploaded, prompt]
            if video_start_sec is not None and video_end_sec is not None:
                contents = [
                    types.Part(
                        file_data=types.FileData(
                            file_uri=uploaded.uri,
                            mime_type=mime_type or "video/mp4",
                        ),
                        video_metadata=types.VideoMetadata(
                            start_offset=f"{video_start_sec}s",
                            end_offset=f"{video_end_sec}s",
                            fps=video_fps,
                        ),
                    ),
                    prompt,
                ]

            def _generate(*, retry: bool) -> AIResponse:
                wait_for_rate_limit(self._rl, cancel_event, what=self.provider_id)
                if retry:
                    contents[-1] = prompt + "\n\n" + TRUNCATED_RETRY_PROMPT
                retry_limit = self._max_tokens * 2 if retry and self._max_tokens > 0 else None
                config = self._config_for(
                    temperature,
                    system,
                    structured_output,
                    media_resolution=media_resolution,
                    max_output_tokens=retry_limit,
                )
                if config is None:
                    response = self._client.models.generate_content(model=model, contents=contents)
                else:
                    response = self._client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=config,
                    )
                return _response_from(response)

            retry_what = t("ai.retry_what_video", {"model": model})
            first = self._call_with_retry(lambda: _generate(retry=False), retry_what, model, cancel_event)
            if not is_truncated_response(first):
                return first

            second = self._call_with_retry(lambda: _generate(retry=True), retry_what, model, cancel_event)
            return AIResponse(
                text=second.text,
                token_usage=_sum_usage(first.token_usage, second.token_usage),
                finish_reason=second.finish_reason,
                block_reason=second.block_reason,
            )
        finally:
            if uploaded is not None and uploaded.name is not None:
                with contextlib.suppress(Exception):  # remote cleanup must not mask the analysis result
                    self._client.files.delete(name=uploaded.name)
