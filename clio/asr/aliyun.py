from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from clio.asr.base import ProviderCapabilities, TranscriptSegment
from clio.asr.factory import register_provider
from clio.asr.retry import request_with_retry
from clio.asr.upload import DASHSCOPE_BASE, dashscope_upload, get_api_key
from clio.i18n import t

_POLL_INTERVAL_S = 3
_MAX_NETWORK_RETRIES = 3
_POLL_TIMEOUT_S = 30 * 60


@register_provider
class AliyunASRProvider:
    capabilities = ProviderCapabilities(
        id="aliyun",
        display_name=t("asr.aliyun.display_name"),
        supports_local_file=True,
        requires_public_url=False,
        max_audio_mb=1024,
        supported_languages=["zh", "en", "auto"],
    )

    def __init__(self, config) -> None:
        self._config = config

    def readiness_error(self, environ=None) -> str | None:
        if get_api_key(environ):
            return None
        return t("asr.aliyun.api_key_missing_readiness")

    def transcribe(
        self,
        audio_path: Path,
        language: str,
        progress_callback: Callable[[int], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[TranscriptSegment]:
        api_key = get_api_key()
        if not api_key:
            raise RuntimeError(t("asr.api_key_missing"))

        file_url = dashscope_upload(
            audio_path,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )
        if progress_callback:
            progress_callback(6)

        task_id = self._submit_task(file_url, language, api_key, cancel_event=cancel_event)
        result = self._poll_task(task_id, api_key, progress_callback, cancel_event)

        segments = self._parse_result(result, cancel_event=cancel_event)
        if progress_callback:
            progress_callback(100)
        return segments

    def _submit_task(
        self,
        file_url: str,
        language: str,
        api_key: str,
        *,
        cancel_event: threading.Event | None = None,
    ) -> str:
        lang_hints = [language] if language not in ("auto", "") else ["zh"]
        body = {
            "model": "paraformer-v2",
            "input": {"file_urls": [file_url]},
            "parameters": {"language_hints": lang_hints},
        }
        resp = request_with_retry(
            lambda: httpx.post(
                f"{DASHSCOPE_BASE}/services/audio/asr/transcription",
                json=body,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "X-DashScope-Async": "enable",
                    "X-DashScope-OssResourceResolve": "enable",
                    "Content-Type": "application/json",
                },
                timeout=60,
            ),
            label=t("asr.aliyun.label_submit"),
            attempts=_MAX_NETWORK_RETRIES,
            cancel_event=cancel_event,
        )
        data = resp.json()
        return data["output"]["task_id"]

    def _poll_task(
        self,
        task_id: str,
        api_key: str,
        progress_callback: Callable[[int], None] | None,
        cancel_event: threading.Event | None,
    ) -> dict:
        start = time.monotonic()
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError(t("asr.aliyun.task_cancelled"))
            if time.monotonic() - start > _POLL_TIMEOUT_S:
                raise RuntimeError(t("asr.aliyun.poll_timeout", {"minutes": _POLL_TIMEOUT_S // 60}))

            resp = request_with_retry(
                lambda: httpx.get(
                    f"{DASHSCOPE_BASE}/tasks/{task_id}",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=30,
                ),
                label=t("asr.aliyun.label_poll"),
                attempts=_MAX_NETWORK_RETRIES,
                cancel_event=cancel_event,
            )
            data: dict = resp.json()
            status = data["output"]["task_status"]
            if status == "SUCCEEDED":
                return data
            if status == "FAILED":
                message = data["output"].get("message", t("asr.aliyun.unknown_error"))
                raise RuntimeError(t("asr.aliyun.transcribe_failed", {"message": message}))

            elapsed = time.monotonic() - start
            if progress_callback and _POLL_TIMEOUT_S > 0:
                pct = min(95, int(5 + (elapsed / _POLL_TIMEOUT_S) * 90))
                progress_callback(pct)
            if cancel_event is None:
                time.sleep(_POLL_INTERVAL_S)
            elif cancel_event.wait(_POLL_INTERVAL_S):
                raise RuntimeError(t("asr.aliyun.task_cancelled"))

    def _parse_result(
        self,
        result: dict,
        *,
        cancel_event: threading.Event | None = None,
    ) -> list[TranscriptSegment]:
        output = result.get("output", {})
        results = output.get("results", [])
        if not results:
            raise RuntimeError(t("asr.aliyun.empty_result"))

        first_result = results[0]
        subtask_status = str(first_result.get("subtask_status") or "").upper()
        if subtask_status and subtask_status != "SUCCEEDED":
            message = first_result.get("message") or first_result.get("code") or subtask_status
            raise RuntimeError(t("asr.aliyun.transcribe_failed", {"message": message}))

        transcription_url = first_result.get("transcription_url")
        if not transcription_url:
            raise RuntimeError(t("asr.aliyun.missing_transcription_url"))

        resp = request_with_retry(
            lambda: httpx.get(transcription_url, timeout=60),
            label=t("asr.aliyun.label_download"),
            attempts=_MAX_NETWORK_RETRIES,
            cancel_event=cancel_event,
        )
        transcript_data: dict = resp.json()

        segments: list[TranscriptSegment] = []
        for transcript in transcript_data.get("transcripts", []):
            for s in transcript.get("sentences", []):
                begin_ms = s.get("begin_time", 0)
                end_ms = s.get("end_time", 0)
                text = s.get("text", "")
                if text.strip():
                    segments.append(
                        TranscriptSegment(
                            start=begin_ms / 1000.0,
                            end=end_ms / 1000.0,
                            text=text.strip(),
                        )
                    )
        return segments
