"""Shared pytest fixtures for all test modules."""

from __future__ import annotations

import sys
import time as _real_time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clio import session_log
from clio.ai.factory import _clear_provider_cache
from clio.config import AppConfig, ProviderConfig, ProxyConfig, load_config


class FakeTime:
    """Scripted stand-in for a module-local ``time`` reference.

    Patching ``clio.x.time.monotonic`` mutates the shared ``time`` module, so
    unrelated background threads consume the scripted values and the patched test
    dies with ``StopIteration``. Patch the module-local attribute instead
    (``fake_time('clio.x', ...)``) so only the module under test sees the fake.

    Scripted values are consumed in order and the last one repeats afterwards, so a
    single value acts as a constant clock. With no values, ``monotonic`` falls back
    to the real clock and only ``sleep`` is mocked.
    """

    def __init__(self, *monotonic_values: float) -> None:
        self._values = list(monotonic_values)
        self._calls = 0
        self.sleep = MagicMock()

    def monotonic(self) -> float:
        if not self._values:
            return _real_time.monotonic()
        value = self._values[min(self._calls, len(self._values) - 1)]
        self._calls += 1
        return value

    def __getattr__(self, name: str):
        return getattr(_real_time, name)


@pytest.fixture
def fake_time(monkeypatch: pytest.MonkeyPatch):
    """Install a :class:`FakeTime` on a module-local ``time`` reference.

    Usage: ``fake_time('clio.progress', 0.0, 2.0)``.
    """

    def _install(module: str, *monotonic_values: float) -> FakeTime:
        fake = FakeTime(*monotonic_values)
        monkeypatch.setattr(f"{module}.time", fake)
        return fake

    return _install


@pytest.fixture(autouse=True)
def _mock_ctranslate2() -> None:
    """Mock ctranslate2 so tests that patch it don't fail on CI (where it's not installed)."""
    if "ctranslate2" not in sys.modules:
        fake = MagicMock()
        fake.get_cuda_device_count.return_value = 0
        sys.modules["ctranslate2"] = fake


@pytest.fixture(autouse=True)
def _clear_ai_cache() -> None:
    """Clear the AI provider cache between tests to avoid cross-test pollution."""
    _clear_provider_cache()


@pytest.fixture(autouse=True)
def _clear_session_log() -> None:
    """Clear session log buffer between tests to avoid cross-test pollution."""
    session_log.clear()


@pytest.fixture(autouse=True)
def _reset_logging_state() -> None:
    """Reset clio.log global state between tests.

    CLI tests run the real ``main()`` which calls ``setup_logging()`` without
    a matching ``teardown_logging()``. If the logging globals leak into later
    tests, the next ``setup_logging()`` returns early (already initialized) and
    ``test_log``'s stdout/stderr restore assertions fail depending on order.
    """
    from clio import log as _log

    _log.teardown_logging()


@pytest.fixture
def tmp_config(tmp_path: Path) -> Path:
    """Create a minimal config.yaml + project.yaml at tmp_path and return its path."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "config_version: V2\n"
        "proxy:\n"
        "  enabled: false\n"
        "ai:\n"
        "  providers:\n"
        "    gemini:\n"
        "      type: gemini\n"
        "      api_key: test-gemini-key\n"
        "      models: [gemini-2.5-flash, gemini-2.0-flash]\n"
        "    deepseek:\n"
        "      type: openai\n"
        "      api_key: test-deepseek-key\n"
        "      base_url: https://api.deepseek.com/v1\n"
        "compress:\n"
        "  fps: 15\n"
        "  remove_audio: true\n",
        encoding="utf-8",
    )
    proj = tmp_path / "project.yaml"
    proj.write_text(
        "paths:\n"
        "  input_dir: .\n"
        "  output_dir: ./output\n"
        "ai:\n"
        "  context: ''\n"
        "  tasks:\n"
        "    video_analyze:\n"
        "      provider: gemini\n"
        "      model: gemini-2.5-flash\n"
        "    voiceover:\n"
        "      provider: deepseek\n"
        "      model: deepseek-chat\n"
        "    vlog_plan:\n"
        "      provider: deepseek\n"
        "      model: deepseek-chat\n"
        "compress:\n"
        "  target_size_mb: 5\n"
        "  max_width: 640\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def empty_dir(tmp_path: Path) -> Path:
    """Return a clean temp directory."""
    return tmp_path


@pytest.fixture
def config_yaml_content() -> str:
    """Return the standard config.yaml content as a string."""
    return (
        "config_version: V2\n"
        "proxy:\n"
        "  enabled: false\n"
        "ai:\n"
        "  providers:\n"
        "    gemini:\n"
        "      type: gemini\n"
        "      api_key_env: GEMINI_API_KEY\n"
        "    deepseek:\n"
        "      type: openai\n"
        "      api_key_env: DEEPSEEK_API_KEY\n"
        "      base_url: https://api.deepseek.com/v1\n"
    )


@pytest.fixture
def loaded_config(tmp_config: Path) -> AppConfig:
    """Load a full AppConfig from a tmp_config (with project.yaml)."""
    return load_config(tmp_config / "config.yaml", project_dir=tmp_config)


@pytest.fixture
def gemini_provider_cfg(loaded_config: AppConfig) -> ProviderConfig:
    """Extract the gemini provider config from a loaded config."""
    return loaded_config.ai.providers["gemini"]


@pytest.fixture
def proxy_cfg() -> ProxyConfig:
    """Return a disabled proxy config."""
    return ProxyConfig(enabled=False, url="")
