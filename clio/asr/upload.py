from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

import httpx

from clio.asr.retry import request_with_retry
from clio.i18n import t

DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/api/v1"


class TempUploadStrategy(Protocol):
    def upload(self, audio_path: Path) -> str: ...


def get_api_key(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    return env.get("DASHSCOPE_API_KEY", "")


def dashscope_upload(
    audio_path: Path,
    model: str = "paraformer-v2",
    progress_callback: Callable[[int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> str:
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError(t("asr.upload.cancelled"))
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError(t("asr.api_key_missing"))

    resp = request_with_retry(
        lambda: httpx.get(
            f"{DASHSCOPE_BASE}/uploads",
            params={"action": "getPolicy", "model": model},
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=30,
        ),
        label=t("asr.upload.label_policy"),
        cancel_event=cancel_event,
    )
    policy: dict[str, Any] = resp.json()["data"]

    file_name = audio_path.name
    key = f"{policy['upload_dir']}/{file_name}"

    if progress_callback:
        progress_callback(2)

    def upload_file() -> httpx.Response:
        with open(audio_path, "rb") as f:
            return httpx.post(
                policy["upload_host"],
                data={
                    "OSSAccessKeyId": policy["oss_access_key_id"],
                    "policy": policy["policy"],
                    "Signature": policy["signature"],
                    "key": key,
                    "x-oss-object-acl": policy["x_oss_object_acl"],
                    "x-oss-forbid-overwrite": policy["x_oss_forbid_overwrite"],
                    "success_action_status": "200",
                },
                files={"file": (file_name, f)},
                timeout=300,
            )

    request_with_retry(upload_file, label=t("asr.upload.label_upload"), cancel_event=cancel_event)
    if progress_callback:
        progress_callback(5)
    return f"oss://{key}"
