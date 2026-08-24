from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from clio.asr.base import ProviderCapabilities, TranscriptSegment
from clio.asr.factory import register_provider
from clio.asr.upload import DASHSCOPE_BASE, dashscope_upload, get_api_key

_POLL_INTERVAL_S = 3
_MAX_NETWORK_RETRIES = 3
_POLL_TIMEOUT_S = 30 * 60


@register_provider
class AliyunASRProvider:
    capabilities = ProviderCapabilities(
        id="aliyun",
        display_name="阿里云 Paraformer",
        supports_local_file=True,
        requires_public_url=False,
        max_audio_mb=1024,
        supported_languages=["zh", "en", "auto"],
    )

    def __init__(self, config) -> None:
        self._config = config

    def transcribe(
        self,
        audio_path: Path,
        language: str,
        progress_callback: Callable[[int], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[TranscriptSegment]:
        api_key = get_api_key()
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY 未设置，请在 .env 中配置")

        file_url = dashscope_upload(audio_path, progress_callback=progress_callback)
        if progress_callback:
            progress_callback(6)

        task_id = self._submit_task(file_url, language, api_key)
        result = self._poll_task(task_id, api_key, progress_callback, cancel_event)

        segments = self._parse_result(result)
        if progress_callback:
            progress_callback(100)
        return segments

    def _submit_task(self, file_url: str, language: str, api_key: str) -> str:
        lang_hints = [language] if language not in ("auto", "") else ["zh"]
        body = {
            "model": "paraformer-v2",
            "input": {"file_urls": [file_url]},
            "parameters": {"language_hints": lang_hints},
        }
        resp = httpx.post(
            f"{DASHSCOPE_BASE}/services/audio/asr/transcription",
            json=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-DashScope-Async": "enable",
                "X-DashScope-OssResourceResolve": "enable",
                "Content-Type": "application/json",
            },
            timeout=60,
        )
        resp.raise_for_status()
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
                raise RuntimeError("云端 ASR 任务已取消")
            if time.monotonic() - start > _POLL_TIMEOUT_S:
                raise RuntimeError(f"云端 ASR 转录超时（{_POLL_TIMEOUT_S // 60} 分钟）")

            resp = None
            for attempt in range(_MAX_NETWORK_RETRIES):
                try:
                    resp = httpx.get(
                        f"{DASHSCOPE_BASE}/tasks/{task_id}",
                        headers={"Authorization": f"Bearer {api_key}"},
                        timeout=30,
                    )
                    resp.raise_for_status()
                    break
                except httpx.RequestError:
                    if attempt == _MAX_NETWORK_RETRIES - 1:
                        raise RuntimeError("查询云端 ASR 状态失败：网络连接不稳定") from None
                    time.sleep(min(0.5 * (2**attempt), 3))
            assert resp is not None
            data: dict = resp.json()
            status = data["output"]["task_status"]
            if status == "SUCCEEDED":
                return data
            if status == "FAILED":
                message = data["output"].get("message", "未知错误")
                raise RuntimeError(f"阿里云 ASR 转录失败: {message}")

            elapsed = time.monotonic() - start
            if progress_callback and _POLL_TIMEOUT_S > 0:
                pct = min(95, int(5 + (elapsed / _POLL_TIMEOUT_S) * 90))
                progress_callback(pct)
            time.sleep(_POLL_INTERVAL_S)

    def _parse_result(self, result: dict) -> list[TranscriptSegment]:
        output = result.get("output", {})
        results = output.get("results", [])
        if not results:
            raise RuntimeError("阿里云 ASR 返回结果为空")

        transcription_url = results[0].get("transcription_url")
        if not transcription_url:
            raise RuntimeError("阿里云 ASR 结果缺少 transcription_url")

        resp = httpx.get(transcription_url, timeout=60)
        resp.raise_for_status()
        transcript_data: dict = resp.json()

        segments: list[TranscriptSegment] = []
        for t in transcript_data.get("transcripts", []):
            for s in t.get("sentences", []):
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
