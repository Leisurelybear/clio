from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

import httpx

from clio.asr.retry import request_with_retry

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
) -> str:
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY 未设置，请在 .env 中配置")

    resp = request_with_retry(
        lambda: httpx.get(
            f"{DASHSCOPE_BASE}/uploads",
            params={"action": "getPolicy", "model": model},
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=30,
        ),
        label="获取音频上传凭证",
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

    request_with_retry(upload_file, label="上传音频")
    if progress_callback:
        progress_callback(5)
    return f"oss://{key}"
