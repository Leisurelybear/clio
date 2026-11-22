"""Route handlers: GET/PUT /api/env — .env file viewer/saver."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from clio.ai.factory import _clear_provider_cache
from clio.ai.provider_security import is_provider_key_env_name
from clio.config import _load_dotenv
from clio.config.loader import parse_dotenv
from clio.credentials.env_policy import _is_blocked_env_name
from clio.credentials.stores import STORE_LOCK
from clio.i18n import t
from clio.utils import validate_within_root, write_secret_atomic

if TYPE_CHECKING:
    from clio.ui.handler_protocol import HandlerProtocol


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
        return validate_within_root(config_path.parent / ".env", config_path.parent)
    return None


def _secret_env_names(handler: HandlerProtocol) -> set[str]:
    names = set(_BUILTIN_SECRET_ENV_NAMES)
    config_path = getattr(handler, "config_path", None)
    if not isinstance(config_path, Path) or not config_path.is_file():
        return names
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError):
        return names
    providers = ((raw.get("ai") or {}).get("providers") or {}) if isinstance(raw, dict) else {}
    for metadata in (raw.get("credentials") or {}).values():
        if isinstance(metadata, dict) and isinstance(metadata.get("env_name"), str):
            names.add(metadata["env_name"].upper())
    if isinstance(providers, dict):
        for provider in providers.values():
            if not isinstance(provider, dict):
                continue
            name = provider.get("api_key_env")
            if isinstance(name, str) and is_provider_key_env_name(name.upper()):
                names.add(name.upper())
    return names


def _mask_env_content(content: str, secret_names: set[str]) -> str:
    lines: list[str] = []
    for line in content.splitlines(keepends=True):
        match = _ENV_ASSIGN_RE.match(line.rstrip("\r\n"))
        if not match or match.group(2).upper() not in secret_names:
            lines.append(line)
            continue
        ending = line[len(line.rstrip("\r\n")) :]
        lines.append(f"{match.group(1)}{match.group(2)}{match.group(3)}********{ending}")
    return "".join(lines)


def _restore_masked_values(content: str, previous_content: str, secret_names: set[str]) -> str:
    previous = parse_dotenv(previous_content)
    previous_literals = {}
    for previous_line in previous_content.splitlines():
        if previous_match := _ENV_ASSIGN_RE.match(previous_line):
            previous_literals[previous_match.group(2)] = previous_match.group(4)
    if not previous:
        return content
    lines: list[str] = []
    for line in content.splitlines(keepends=True):
        match = _ENV_ASSIGN_RE.match(line.rstrip("\r\n"))
        if (
            not match
            or match.group(2).upper() not in secret_names
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
            lines.append(f"{match.group(1)}{key}{match.group(3)}{previous_literals.get(key, old)}{ending}")
    return "".join(lines)


def handle_get_env(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    env_path = _dotenv_path(handler)
    if env_path and env_path.is_file():
        # utf-8-sig: Notepad-saved .env files start with a BOM that would hide the first key.
        text = env_path.read_text(encoding="utf-8-sig")
    else:
        template_path = env_path.parent / ".env.example" if env_path else None
        if template_path and template_path.is_file():
            text = template_path.read_text(encoding="utf-8-sig")
        else:
            text = t("api.env_template_header")
    secret_names = _secret_env_names(handler)
    secret_names.update(key.upper() for key in parse_dotenv(text) if is_provider_key_env_name(key.upper()))
    handler._send_json(
        {
            "path": str(env_path) if env_path else "",
            "content": _mask_env_content(text, secret_names),
            "managed_credentials": sorted(secret_names),
        }
    )


def handle_put_env(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    with STORE_LOCK:
        _put_env_locked(handler, qs, obj)


def _put_env_locked(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    env_path = _dotenv_path(handler)
    if not env_path:
        return handler._send_json({"ok": False, "error": "config_path not available"}, 500)
    content = obj.get("content", "")
    if not isinstance(content, str):
        return handler._send_json({"ok": False, "error": "content must be a string"}, 400)
    previous_content = env_path.read_text(encoding="utf-8-sig") if env_path.is_file() else ""
    secret_names = _secret_env_names(handler)
    secret_names.update(
        key.upper()
        for key in parse_dotenv(previous_content) | parse_dotenv(content)
        if is_provider_key_env_name(key.upper())
    )
    parsed = parse_dotenv(content)
    blocked = sorted(key for key in parsed if _is_blocked_env_name(key))
    if blocked:
        return handler._send_json({"ok": False, "error": f"blocked environment variable(s): {', '.join(blocked)}"}, 400)
    content = _restore_masked_values(content, previous_content, secret_names)
    # An old raw editor tab must not overwrite a managed credential rotation.
    assert handler.config_path is not None
    raw = yaml.safe_load(handler.config_path.read_text(encoding="utf-8")) or {}
    managed = {
        str(meta.get("env_name", "")) for meta in (raw.get("credentials") or {}).values() if isinstance(meta, dict)
    }
    old_values, new_values = parse_dotenv(previous_content), parse_dotenv(content)
    if any(old_values.get(name) != new_values.get(name) for name in managed):
        return handler._send_json(
            {"ok": False, "error": "managed credentials must be changed through Credential Manager"}, 409
        )
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
    from clio.credentials.environment import sync_dotenv

    sync_dotenv(parse_dotenv(content), override=True, previous=parse_dotenv(previous_content))
