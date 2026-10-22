from __future__ import annotations

import mimetypes
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from google import genai
from google.genai import types

from clio.ai.base import AIResponse, TokenUsage
from clio.config import ProviderConfig, ProxyConfig
from clio.i18n import t
from clio.ratelimit import make_rate_limiter
from clio.utils import mask_if_looks_like_key, with_retry

try:
    from google.genai import errors as _genai_errors

    _HAS_GENAI_ERRORS = True
except ImportError:
    _HAS_GENAI_ERRORS = False


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
        self._generation_config = (
            types.GenerateContentConfig(max_output_tokens=cfg.max_tokens) if cfg.max_tokens > 0 else None
        )

    def _is_retryable(self, exc: BaseException) -> bool:
        """判断异常是否应该重试。"""
        if isinstance(exc, OSError):
            return True
        if _HAS_GENAI_ERRORS:
            if isinstance(exc, _genai_errors.ServerError):
                return True
            if isinstance(exc, _genai_errors.ClientError):
                code = getattr(exc, "code", None)
                if code == 429:
                    return True
                status = getattr(exc, "status", "") or ""
                if "RATE_LIMIT" in status.upper() or "RESOURCE_EXHAUSTED" in status.upper():
                    return True
                return False
        return False

    def _retryable_types(self):
        """返回 with_retry 需要的异常类型元组。"""
        types_list: list[type[Exception]] = [OSError]
        if _HAS_GENAI_ERRORS:
            types_list.append(_genai_errors.ClientError)
            types_list.append(_genai_errors.ServerError)
        return tuple(types_list)

    def _call_with_retry(self, fn, what, model):
        """统一的重试入口：将非重试 ClientError 转成 RuntimeError 避免误重试。"""

        def _wrapped():
            try:
                return fn()
            except BaseException as e:
                if self._is_retryable(e):
                    raise
                raise RuntimeError(t("ai.gemini_non_retryable", {"error": e})) from e

        return with_retry(
            _wrapped,
            attempts=self._retry_attempts,
            base_delay=2.0,
            retry_on=self._retryable_types(),
            what=f"Gemini {what}",
            # 用 should_retry 进一步过滤：只有 _is_retryable 为 True 才重试
            should_retry=lambda e: self._is_retryable(e),
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
            try:
                self._client.close()
            except Exception:
                pass

    def _maybe_wait(self) -> None:
        if self._rl is not None:
            wait = self._rl.acquire()
            if wait:
                time.sleep(wait)

    def _extract_usage(self, response) -> TokenUsage | None:
        if not response.usage_metadata:
            return None
        meta = response.usage_metadata
        return TokenUsage(
            prompt_tokens=meta.prompt_token_count or 0,
            completion_tokens=meta.candidates_token_count or 0,
            total_tokens=meta.total_token_count or 0,
        )

    def generate_text(self, prompt: str, model: str) -> AIResponse:
        def _do() -> AIResponse:
            self._maybe_wait()
            if self._generation_config is None:
                response = self._client.models.generate_content(model=model, contents=prompt)
            else:
                response = self._client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=self._generation_config,
                )
            return AIResponse(text=response.text or "", token_usage=self._extract_usage(response))

        return self._call_with_retry(_do, model, model)

    def analyze_video(
        self,
        video_path: str,
        prompt: str,
        model: str,
        progress_callback: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> AIResponse:
        uploaded = None
        try:
            if progress_callback:
                progress_callback(t("ai.progress_uploading"))
            self._maybe_wait()
            # Check cancellation before upload
            if cancel_event and cancel_event.is_set():
                raise RuntimeError(t("ai.video_analysis_cancelled"))

            # 用 BytesIO 避免 google-genai 2.8.0 的 bug：
            # 非 ASCII 文件名会作为 HTTP header 值发送，导致
            # UnicodeEncodeError: 'ascii' codec can't encode character
            p = Path(video_path)
            if p.stat().st_size > 200 * 1024 * 1024:
                raise ValueError(t("ai.file_too_large", {"size": f"{p.stat().st_size / 1024 / 1024:.0f}"}))
            mime_type, _ = mimetypes.guess_type(str(p))
            with open(video_path, "rb") as f:
                uploaded = self._client.files.upload(
                    file=f,
                    config=types.UploadFileConfig(mime_type=mime_type or "video/mp4"),
                )
            if progress_callback:
                progress_callback(t("ai.progress_waiting"))
            # Pass cancel_event to _wait_for_file
            uploaded = self._wait_for_file(uploaded, on_progress=progress_callback, cancel_event=cancel_event)

            if progress_callback:
                progress_callback(t("ai.progress_analyzing"))

            def _do() -> AIResponse:
                self._maybe_wait()
                if self._generation_config is None:
                    response = self._client.models.generate_content(model=model, contents=[uploaded, prompt])
                else:
                    response = self._client.models.generate_content(
                        model=model,
                        contents=[uploaded, prompt],
                        config=self._generation_config,
                    )
                return AIResponse(text=response.text or "", token_usage=self._extract_usage(response))

            return self._call_with_retry(_do, t("ai.retry_what_video", {"model": model}), model)
        finally:
            if uploaded is not None and uploaded.name is not None:
                try:
                    self._client.files.delete(name=uploaded.name)
                except Exception:
                    pass
