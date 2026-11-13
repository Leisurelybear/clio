"""Route handlers: GET/PUT /api/env — .env file viewer/saver."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from clio.ai.factory import _clear_provider_cache
from clio.ai.provider_security import is_provider_key_env_name
from clio.config import _load_dotenv
from clio.config.loader import parse_dotenv
from clio.i18n import t
from clio.utils import write_secret_atomic

if TYPE_CHECKING:
    from clio.ui.handler_protocol import HandlerProtocol


_ENV_ASSIGN_RE = re.compile(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*)$")
_MASKED_VALUES = {"***", "********", "••••••••••"}
_PROJECT_ENV_NAMES = {
    "CLIO_LANG",
    "CLIO_FFMPEG_DIR",
    "FFMPEG_HOME",
    "HF_ENDPOINT",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "MKL_NUM_THREADS",
    "OMP_NUM_THREADS",
}
# These variables can change executable/module resolution or inject code into
# child processes after the saved .env is loaded.
_BLOCKED_ENV_NAMES = {
    "BASH_ENV",
    "BASHOPTS",
    "BROWSER",
    "CDPATH",
    "CLASSPATH",
    "COMSPEC",
    "EDITOR",
    "ENV",
    "GIT_ASKPASS",
    "GIT_EXEC_PATH",
    "GIT_PROXY_COMMAND",
    "GIT_SSH",
    "GIT_SSH_COMMAND",
    "IFS",
    "JAVA_TOOL_OPTIONS",
    "JDK_JAVA_OPTIONS",
    "LESSOPEN",
    "LESSCLOSE",
    "MANPAGER",
    "NODE_OPTIONS",
    "NODE_PATH",
    "PAGER",
    "PATH",
    "PATHEXT",
    "PERL5LIB",
    "PERL5OPT",
    "PROMPT_COMMAND",
    "PYTHONBREAKPOINT",
    "PYTHONHOME",
    "PYTHONINSPECT",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PYTHONUSERBASE",
    "RUBYLIB",
    "RUBYOPT",
    "SHELL",
    "SHELLOPTS",
    "SSH_ASKPASS",
    "SYSTEMROOT",
    "VISUAL",
    "WINDIR",
    "_JAVA_OPTIONS",
}
_BLOCKED_ENV_PREFIXES = ("DYLD_", "GIT_CONFIG_", "LD_")
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
    if isinstance(providers, dict):
        for provider in providers.values():
            if not isinstance(provider, dict):
                continue
            name = provider.get("api_key_env")
            if isinstance(name, str) and is_provider_key_env_name(name.upper()):
                names.add(name.upper())
    return names


def _is_blocked_env_name(name: str) -> bool:
    normalized = name.strip().upper()
    if normalized in _PROJECT_ENV_NAMES:
        return False
    return normalized in _BLOCKED_ENV_NAMES or normalized.startswith(_BLOCKED_ENV_PREFIXES)


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
    secret_names = _secret_env_names(handler)
    secret_names.update(key.upper() for key in parse_dotenv(text) if is_provider_key_env_name(key.upper()))
    handler._send_json({"path": str(env_path) if env_path else "", "content": _mask_env_content(text, secret_names)})


def handle_put_env(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    env_path = _dotenv_path(handler)
    if not env_path:
        return handler._send_json({"ok": False, "error": "config_path not available"}, 500)
    content = obj.get("content", "")
    if not isinstance(content, str):
        return handler._send_json({"ok": False, "error": "content must be a string"}, 400)
    previous_content = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
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
