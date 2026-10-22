"""Route handlers: GET/PUT /api/env — .env file viewer/saver."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clio.ai.factory import _clear_provider_cache
from clio.config import _load_dotenv  # noqa: F401 — kept for test mocks
from clio.i18n import t
from clio.utils import write_secret_atomic

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
        template_path = env_path.parent / ".env.example" if env_path else None
        if template_path and template_path.is_file():
            text = template_path.read_text(encoding="utf-8")
        else:
            text = t("api.env_template_header")
    handler._send_json({"path": str(env_path) if env_path else "", "content": text})


def handle_put_env(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    env_path = _dotenv_path(handler)
    if not env_path:
        return handler._send_json({"ok": False, "error": "config_path not available"}, 500)
    content = obj.get("content", "")
    if not isinstance(content, str):
        return handler._send_json({"ok": False, "error": "content must be a string"}, 400)
    previous_content = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    env_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        write_secret_atomic(env_path, content)
    except PermissionError as e:
        return handler._send_json({"ok": False, "error": str(e)}, 403)
    _load_dotenv(env_path.parent, override=True)
    _sync_env_to_environ(content, env_path.parent, previous_content=previous_content)
    handler.__class__._config_cache.invalidate_all()
    _clear_provider_cache()
    handler._send_json({"ok": True, "path": str(env_path)})


def _sync_env_to_environ(content: str, config_dir: Path, *, previous_content: str = "") -> None:
    """Parse .env content and sync to os.environ: add/update present keys, remove absent ones."""

    def parse(raw: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in raw.splitlines():
            line = line.strip()
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
            if m and not line.startswith("#"):
                values[m.group(1)] = m.group(2).strip().strip('"').strip("'")
        return values

    new_keys = parse(content)
    previous_keys = parse(previous_content)
    for key, old_value in previous_keys.items():
        if key not in new_keys and os.environ.get(key) == old_value:
            del os.environ[key]
    for key in list(os.environ.keys()):
        if key.startswith(("DEEPSEEK_", "GEMINI_", "OPENAI_", "MOONSHOT_", "TONGYI_", "DASHSCOPE_")):
            if key in new_keys:
                os.environ[key] = new_keys[key]
            else:
                del os.environ[key]
    for key, val in new_keys.items():
        os.environ[key] = val
