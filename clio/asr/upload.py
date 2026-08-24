from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import httpx

DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/api/v1"


class TempUploadStrategy(Protocol):
    def upload(self, audio_path: Path) -> str: ...


def get_api_key() -> str:
    return os.environ.get("DASHSCOPE_API_KEY", "")


def dashscope_upload(
    audio_path: Path,
    model: str = "paraformer-v2",
    progress_callback: Callable[[int], None] | None = None,
) -> str:
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY 未设置，请在 .env 中配置")

    resp = None
    for attempt in range(3):
        try:
            resp = httpx.get(
                f"{DASHSCOPE_BASE}/uploads",
                params={"action": "getPolicy", "model": model},
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                timeout=30,
            )
            resp.raise_for_status()
            break
        except httpx.RequestError:
            if attempt == 2:
                raise
            time.sleep(0.5 * (2**attempt))
    assert resp is not None
    policy: dict[str, Any] = resp.json()["data"]

    file_name = audio_path.name
    key = f"{policy['upload_dir']}/{file_name}"

    if progress_callback:
        progress_callback(2)
    with open(audio_path, "rb") as f:
        resp = httpx.post(
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
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"音频上传失败（HTTP {e.response.status_code}），请检查网络后重试") from e
    if progress_callback:
        progress_callback(5)
    return f"oss://{key}"
