"""Route handlers: GET/PUT /api/env — .env file viewer/saver."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clio.ai.factory import _clear_provider_cache
from clio.config import _load_dotenv  # noqa: F401 — kept for test mocks
from clio.utils import write_text_atomic

if TYPE_CHECKING:
    from clio.ui.handler_protocol import HandlerProtocol


def _dotenv_path(handler: HandlerProtocol) -> Path | None:
    config_path: Path | None = handler.config_path
    if config_path and config_path.is_file():
        return config_path.parent / ".env"
    return None


def handle_get_env(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    env_path = _dotenv_path(handler)
    if env_path and env_path.is_file():
        text = env_path.read_text(encoding="utf-8")
    else:
        text = (
            "# 在此设置环境变量，每行 KEY=VALUE\n"
            "# 示例:\n"
            "# DEEPSEEK_API_KEY=your_key_here\n"
            "# GEMINI_API_KEY=your_key_here\n"
        )
    handler._send_json({"path": str(env_path) if env_path else "", "content": text})


def handle_put_env(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    env_path = _dotenv_path(handler)
    if not env_path:
        return handler._send_json({"ok": False, "error": "config_path not available"}, 500)
    content = obj.get("content", "")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(env_path, content)
    _load_dotenv(env_path.parent, override=True)
    _sync_env_to_environ(content, env_path.parent)
    handler.__class__._config_cache.invalidate_all()
    _clear_provider_cache()
    handler._send_json({"ok": True, "path": str(env_path)})


def _sync_env_to_environ(content: str, config_dir: Path) -> None:
    """Parse .env content and sync to os.environ: add/update present keys, remove absent ones."""
    new_keys: dict[str, str] = {}
    for line in content.splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line.strip())
        if m and not line.strip().startswith("#"):
            new_keys[m.group(1)] = m.group(2)
    for key in list(os.environ.keys()):
        if key.startswith(("DEEPSEEK_", "GEMINI_", "OPENAI_", "MOONSHOT_", "TONGYI_")):
            if key in new_keys:
                os.environ[key] = new_keys[key]
            else:
                del os.environ[key]
    for key, val in new_keys.items():
        os.environ[key] = val
