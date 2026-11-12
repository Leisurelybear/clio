"""Route handlers: GET/PUT /api/env — .env file viewer/saver."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from clio.ai.factory import _clear_provider_cache
from clio.config import _load_dotenv
from clio.config.loader import parse_dotenv
from clio.i18n import t
from clio.utils import write_secret_atomic

if TYPE_CHECKING:
    from clio.ui.handler_protocol import HandlerProtocol


_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ENV_ASSIGN_RE = re.compile(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*)$")
_MASKED_VALUES = {"***", "********", "••••••••••"}
_BUILTIN_SECRET_ENV_NAMES = {
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "MOONSHOT_API_KEY",
    "TONGYI_API_KEY",
    "DASHSCOPE_API_KEY",
    "ALIYUN_ASR_API_KEY",
    "ALIYUN_ACCESS_KEY_ID",
    "ALIYUN_ACCESS_KEY_SECRET",
    "BAIDU_APP_KEY",
    "BAIDU_SECRET_KEY",
    "BAIDU_ASR_API_KEY",
    "BAIDU_ASR_SECRET_KEY",
}


def _dotenv_path(handler: HandlerProtocol) -> Path | None:
    config_path: Path | None = handler.config_path
    if config_path and config_path.is_file():
        return config_path.parent / ".env"
    return None


def _allowed_env_names(handler: HandlerProtocol) -> set[str]:
    allowed = set(_BUILTIN_SECRET_ENV_NAMES)
    config_path = getattr(handler, "config_path", None)
    if not isinstance(config_path, Path) or not config_path.is_file():
        return allowed
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError):
        return allowed
    providers = ((raw.get("ai") or {}).get("providers") or {}) if isinstance(raw, dict) else {}
    if isinstance(providers, dict):
        for provider in providers.values():
            if not isinstance(provider, dict):
                continue
            name = provider.get("api_key_env")
            if isinstance(name, str) and _ENV_NAME_RE.fullmatch(name):
                allowed.add(name)
    return allowed


def _mask_env_content(content: str, allowed: set[str]) -> str:
    lines: list[str] = []
    for line in content.splitlines(keepends=True):
        match = _ENV_ASSIGN_RE.match(line.rstrip("\r\n"))
        if not match or match.group(2) not in allowed:
            lines.append(line)
            continue
        ending = line[len(line.rstrip("\r\n")) :]
        lines.append(f"{match.group(1)}{match.group(2)}{match.group(3)}********{ending}")
    return "".join(lines)


def _restore_masked_values(content: str, previous_content: str, allowed: set[str]) -> str:
    previous = parse_dotenv(previous_content)
    if not previous:
        return content
    lines: list[str] = []
    for line in content.splitlines(keepends=True):
        match = _ENV_ASSIGN_RE.match(line.rstrip("\r\n"))
        if (
            not match
            or match.group(2) not in allowed
            or match.group(4).strip().strip('"').strip("'") not in _MASKED_VALUES
        ):
            lines.append(line)
            continue
        key = match.group(2)
        old = previous.get(key)
        ending = line[len(line.rstrip("\r\n")) :]
        if old is None:
            lines.append(line)
        else:
            lines.append(f"{match.group(1)}{key}{match.group(3)}{old}{ending}")
    return "".join(lines)


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
    handler._send_json(
        {"path": str(env_path) if env_path else "", "content": _mask_env_content(text, _allowed_env_names(handler))}
    )


def handle_put_env(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    env_path = _dotenv_path(handler)
    if not env_path:
        return handler._send_json({"ok": False, "error": "config_path not available"}, 500)
    content = obj.get("content", "")
    if not isinstance(content, str):
        return handler._send_json({"ok": False, "error": "content must be a string"}, 400)
    previous_content = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    allowed = _allowed_env_names(handler)
    parsed = parse_dotenv(content)
    invalid = sorted(key for key in parsed if key not in allowed)
    if invalid:
        return handler._send_json(
            {"ok": False, "error": f"unsupported environment variable(s): {', '.join(invalid)}"}, 400
        )
    content = _restore_masked_values(content, previous_content, allowed)
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
    """Sync only keys managed by the previous/new .env content."""
    del config_dir
    new_keys = parse_dotenv(content)
    previous_keys = parse_dotenv(previous_content)
    for key, old_value in previous_keys.items():
        if key not in new_keys and os.environ.get(key) == old_value:
            del os.environ[key]
    for key, val in new_keys.items():
        os.environ[key] = val
