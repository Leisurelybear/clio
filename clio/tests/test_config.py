"""Tests for clio/config — pure functions and config loading."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from clio.config import (
    WhisperConfig,
    _load_context,
    _path,
    _resolve_api_key,
    _validate_config,
    deep_merge,
    load_config,
)
from clio.config.models import (
    AnalyzeConfig,
    AppConfig,
    ExportConfig,
    GlobalAIConfig,
    GlobalCompressConfig,
    GlobalConfig,
    PlanConfig,
    ProjectAIConfig,
    ProjectConfig,
    ProjectPathsConfig,
    ProjectWhisperConfig,
    ProviderConfig,
    ProxyConfig,
    ScriptConfig,
    TaskConfig,
)

# ── deep_merge ──────────────────────────────────────────────────────


class TestDeepMerge:
    def test_basic_override(self):
        base = {"a": 1, "b": 2}
        override = {"b": 99}
        assert deep_merge(base, override) == {"a": 1, "b": 99}

    def test_nested_dict_merge(self):
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        override = {"a": {"y": 999, "z": 4}}
        result = deep_merge(base, override)
        assert result == {"a": {"x": 1, "y": 999, "z": 4}, "b": 3}

    def test_new_keys_from_override(self):
        base = {"a": 1}
        override = {"b": 2}
        assert deep_merge(base, override) == {"a": 1, "b": 2}

    def test_non_dict_override(self):
        base = {"a": {"nested": "keep"}}
        override = {"a": "replace"}
        assert deep_merge(base, override) == {"a": "replace"}

    def test_none_override(self):
        base = {"a": 1, "b": 2}
        override = {"a": None}
        assert deep_merge(base, override) == {"a": None, "b": 2}

    def test_empty_override(self):
        base = {"a": 1}
        assert deep_merge(base, {}) == {"a": 1}

    def test_empty_base(self):
        assert deep_merge({}, {"a": 1}) == {"a": 1}

    def test_deeply_nested(self):
        base = {"one": {"two": {"three": 3}}}
        override = {"one": {"two": {"three": 99, "four": 4}}}
        result = deep_merge(base, override)
        assert result == {"one": {"two": {"three": 99, "four": 4}}}

    def test_does_not_mutate_inputs(self):
        base = {"a": {"b": 1}}
        override = {"a": {"b": 2}}
        deep_merge(base, override)
        assert base == {"a": {"b": 1}}
        assert override == {"a": {"b": 2}}

    def test_list_replaced_not_merged(self):
        base = {"items": [1, 2, 3]}
        override = {"items": [4, 5]}
        assert deep_merge(base, override) == {"items": [4, 5]}


# ── _path ───────────────────────────────────────────────────────────


class TestPath:
    def test_none_raises(self):
        with pytest.raises(ValueError, match="路径不能为空"):
            _path(None)

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="路径不能为空"):
            _path("")

    def test_absolute_path(self):
        result = _path("/some/absolute/path")
        assert result == Path("/some/absolute/path").resolve()

    def test_relative_with_base(self):
        base = Path("/base")
        result = _path("sub/file.txt", base)
        assert result == (base / "sub/file.txt").resolve()

    def test_relative_without_base(self):
        result = _path("relative/path")
        assert result == Path("relative/path").resolve()


# ── _resolve_api_key ───────────────────────────────────────────────


class TestResolveApiKey:
    def test_env_var_takes_priority(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "env_value")
        raw = {"api_key_env": "MY_KEY", "api_key": "inline_value"}
        assert _resolve_api_key(raw) == "env_value"

    def test_env_var_missing_falls_to_inline(self):
        raw = {"api_key_env": "NONEXISTENT_VAR", "api_key": "inline_value"}
        assert _resolve_api_key(raw) == "inline_value"

    def test_no_env_var_returns_inline(self):
        raw = {"api_key": "direct_key"}
        assert _resolve_api_key(raw) == "direct_key"

    def test_empty_env_name_returns_inline(self):
        raw = {"api_key_env": "", "api_key": "fallback"}
        assert _resolve_api_key(raw) == "fallback"


# ── _load_context ──────────────────────────────────────────────────


class TestLoadContext:
    def test_inline_takes_priority(self, tmp_path):
        ai_raw = {"context": "inline text", "context_file": "ignored.md"}
        assert _load_context(ai_raw, tmp_path) == "inline text"

    def test_context_file_resolved_from_base(self, tmp_path):
        ctx_file = tmp_path / "mycontext.md"
        ctx_file.write_text("file content", encoding="utf-8")
        ai_raw = {"context_file": "mycontext.md"}
        assert _load_context(ai_raw, tmp_path) == "file content"

    def test_context_file_from_project_dir_first(self, tmp_path):
        base = tmp_path / "config_dir"
        base.mkdir()
        proj = tmp_path / "project_dir"
        proj.mkdir()
        (proj / "ctx.md").write_text("project context", encoding="utf-8")

        ai_raw = {"context_file": "ctx.md"}
        result = _load_context(ai_raw, base, project_dir=proj)
        assert result == "project context"

    def test_fallback_to_base_when_not_in_project(self, tmp_path):
        base = tmp_path / "config_dir"
        base.mkdir()
        (base / "ctx.md").write_text("base context", encoding="utf-8")

        ai_raw = {"context_file": "ctx.md"}
        result = _load_context(ai_raw, base, project_dir=tmp_path / "nonexistent")
        assert result == "base context"

    def test_missing_file_returns_empty(self, tmp_path):
        ai_raw = {"context_file": "does_not_exist.md"}
        assert _load_context(ai_raw, tmp_path) == ""

    def test_no_context_configured(self, tmp_path):
        assert _load_context({}, tmp_path) == ""


# ── _validate_config ────────────────────────────────────────────────


class TestValidateConfig:
    def test_valid_config_passes(self):
        cfg = AppConfig(
            global_cfg=GlobalConfig(
                ai=GlobalAIConfig(
                    providers={"gemini": ProviderConfig(name="gemini", type="gemini", api_key="k")},
                ),
            ),
            project_cfg=ProjectConfig(
                ai=ProjectAIConfig(
                    tasks={"task": TaskConfig(provider="gemini", model="m")},
                ),
            ),
        )
        assert _validate_config(cfg) is None

    def test_proxy_enabled_no_url_raises(self):
        cfg = AppConfig(
            global_cfg=GlobalConfig(
                proxy=ProxyConfig(enabled=True, url=""),
            ),
        )
        with pytest.raises(ValueError, match="proxy"):
            _validate_config(cfg)

    def test_subdir_absolute_escapes_root(self):
        cfg = AppConfig(
            global_cfg=GlobalConfig(),
            project_cfg=ProjectConfig(analyze=AnalyzeConfig(compressed_subdir="/etc/passwd")),
        )
        with pytest.raises(ValueError, match="compressed_subdir"):
            _validate_config(cfg)

    def test_subdir_dotdot_escapes_root(self):
        cfg = AppConfig(
            global_cfg=GlobalConfig(),
            project_cfg=ProjectConfig(plan=PlanConfig(plans_subdir="../outside")),
        )
        with pytest.raises(ValueError, match="plans_subdir"):
            _validate_config(cfg)

    def test_subdir_tilde_accepted_only_as_relative(self):
        cfg = AppConfig(
            global_cfg=GlobalConfig(),
            project_cfg=ProjectConfig(whisper=WhisperConfig(transcripts_subdir="~/home")),
        )
        with pytest.raises(ValueError, match="transcripts_subdir"):
            _validate_config(cfg)

    def test_subdir_depth_allowed(self):
        cfg = AppConfig(
            global_cfg=GlobalConfig(),
            project_cfg=ProjectConfig(script=ScriptConfig(scripts_subdir="nested/deep")),
        )
        assert _validate_config(cfg) is None

    def test_output_subdir_symlink_rejected_at_join(self, tmp_path: Path):
        """GAP-P1-03: join-time containment must reject a planted subdir symlink."""
        out = tmp_path / "out"
        out.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (out / "compressed").symlink_to(outside)
        cfg = AppConfig(
            global_cfg=GlobalConfig(),
            project_cfg=ProjectConfig(paths=ProjectPathsConfig(output_dir=out)),
        )
        with pytest.raises(ValueError, match=r"^symlink not allowed:"):
            _ = cfg.compressed_dir

    def test_output_subdir_join_stays_inside_root(self, tmp_path: Path):
        out = tmp_path / "out"
        out.mkdir()
        cfg = AppConfig(
            global_cfg=GlobalConfig(),
            project_cfg=ProjectConfig(
                paths=ProjectPathsConfig(output_dir=out),
                script=ScriptConfig(scripts_subdir="nested/deep"),
            ),
        )
        assert cfg.scripts_dir == (out / "nested" / "deep").resolve()

    def test_task_refers_to_nonexistent_provider(self):
        cfg = AppConfig(
            global_cfg=GlobalConfig(
                ai=GlobalAIConfig(
                    providers={"gemini": ProviderConfig(name="gemini", type="gemini", api_key="k")},
                ),
            ),
            project_cfg=ProjectConfig(
                ai=ProjectAIConfig(
                    tasks={"task": TaskConfig(provider="nonexistent", model="m")},
                ),
            ),
        )
        with pytest.raises(ValueError, match="task"):
            _validate_config(cfg)

    def test_empty_provider_set_with_task(self):
        cfg = AppConfig(
            global_cfg=GlobalConfig(),
            project_cfg=ProjectConfig(
                ai=ProjectAIConfig(
                    tasks={"task": TaskConfig(provider="some_provider", model="m")},
                ),
            ),
        )
        with pytest.raises(ValueError, match="<无>"):
            _validate_config(cfg)

    def test_task_model_outside_provider_models_is_warning_not_fatal(self, caplog):
        """R-040 F-2: a bound model outside the provider's declared list must not crash load."""
        provider = ProviderConfig(
            name="deepseek",
            type="openai",
            api_key="k",
            models=["deepseek-chat", "deepseek-reasoner"],
        )
        cfg = AppConfig(
            global_cfg=GlobalConfig(ai=GlobalAIConfig(providers={"deepseek": provider})),
            project_cfg=ProjectConfig(
                ai=ProjectAIConfig(
                    tasks={"voiceover": TaskConfig(provider="deepseek", model="deepseek-v4-flash")},
                ),
            ),
        )
        with caplog.at_level("WARNING", logger="clio.config"):
            _validate_config(cfg)
        assert any("deepseek-v4-flash" in r.message for r in caplog.records), caplog.text

    @pytest.mark.parametrize("fps,crf", [(0, 32), (15, -1), (15, 52)])
    def test_rejects_invalid_global_compress_values(self, fps, crf):
        cfg = AppConfig(global_cfg=GlobalConfig(compress=GlobalCompressConfig(fps=fps, crf=crf)))
        with pytest.raises(ValueError, match="compress"):
            _validate_config(cfg)

    @pytest.mark.parametrize("timeout,poll", [(0, 5), (120, 0)])
    def test_rejects_non_positive_gemini_timing(self, timeout, poll):
        provider = ProviderConfig(
            name="gemini",
            type="gemini",
            api_key="k",
            timeout_sec=timeout,
            poll_interval_sec=poll,
        )
        cfg = AppConfig(global_cfg=GlobalConfig(ai=GlobalAIConfig(providers={"gemini": provider})))
        with pytest.raises(ValueError, match="ai.providers.gemini"):
            _validate_config(cfg)

    def test_rejects_unknown_canvas_ratio(self):
        cfg = AppConfig(
            global_cfg=GlobalConfig(),
            project_cfg=ProjectConfig(export=ExportConfig(canvas_ratio="4:3")),
        )
        with pytest.raises(ValueError, match="canvas_ratio"):
            _validate_config(cfg)

    def test_validates_project_whisper_values(self):
        cfg = AppConfig(
            global_cfg=GlobalConfig(),
            project_cfg=ProjectConfig(whisper=ProjectWhisperConfig(device="gpu")),
        )
        with pytest.raises(ValueError, match="whisper.device"):
            _validate_config(cfg)

    # ── load_config ─────────────────────────────────────────────────────

    @pytest.mark.parametrize(
        ("field_name", "mutate"),
        [
            ("analyze.max_workers", lambda cfg: setattr(cfg.project_cfg.analyze, "max_workers", 0)),
            ("compress.target_size_mb", lambda cfg: setattr(cfg.project_cfg.compress, "target_size_mb", 0)),
            ("compress.max_width", lambda cfg: setattr(cfg.project_cfg.compress, "max_width", 0)),
            ("naming.index_width", lambda cfg: setattr(cfg.global_cfg.naming, "index_width", 0)),
            ("ai.provider_ttl_min", lambda cfg: setattr(cfg.global_cfg.ai, "provider_ttl_min", -1)),
            (
                "ai.providers.gemini.requests_per_minute",
                lambda cfg: setattr(cfg.global_cfg.ai.providers["gemini"], "requests_per_minute", -1),
            ),
            (
                "ai.providers.gemini.retry_attempts",
                lambda cfg: setattr(cfg.global_cfg.ai.providers["gemini"], "retry_attempts", -1),
            ),
            (
                "ai.providers.gemini.max_tokens",
                lambda cfg: setattr(cfg.global_cfg.ai.providers["gemini"], "max_tokens", -1),
            ),
        ],
    )
    def test_numeric_ranges_are_validated(self, field_name, mutate):
        cfg = AppConfig(
            global_cfg=GlobalConfig(
                ai=GlobalAIConfig(
                    providers={"gemini": ProviderConfig(name="gemini", type="gemini", api_key="k")},
                ),
            ),
            project_cfg=ProjectConfig(
                ai=ProjectAIConfig(
                    tasks={"task": TaskConfig(provider="gemini", model="m")},
                ),
            ),
        )
        mutate(cfg)

        with pytest.raises(ValueError, match=field_name.replace(".", r"\.")):
            _validate_config(cfg)


class TestLoadConfig:
    def test_minimal_config(self, tmp_config):
        cfg = load_config(tmp_config / "config.yaml")
        assert isinstance(cfg, AppConfig)
        assert cfg.compress.fps == 15
        assert cfg.compress.target_size_mb == 5
        assert cfg.proxy.enabled is False

    def test_missing_file_raises(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        missing = blocker / "config.yaml"
        with pytest.raises(FileNotFoundError):
            load_config(missing)

    def test_example_config_path_resolves_repo_root(self):
        from clio.config.loader import _example_config_path

        path = _example_config_path()
        assert path is not None
        assert path.is_file()
        assert path.name == "config.example.yaml"
        assert "ai:" in path.read_text(encoding="utf-8")

    def test_auto_generates_missing_config(self, tmp_path):
        target = tmp_path / "nested" / "config.yaml"
        cfg = load_config(target)
        assert target.is_file()
        assert "ai:" in target.read_text(encoding="utf-8")
        assert cfg.compress.fps == 15
        assert cfg.proxy.enabled is False

    def test_missing_file_raises_when_generation_blocked(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        missing = blocker / "config.yaml"
        with pytest.raises(FileNotFoundError):
            load_config(missing)

    def test_provider_ttl_min_from_config(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "paths:\n  input_dir: .\n  output_dir: ./output\n"
            "proxy:\n  enabled: false\n"
            "ai:\n  provider_ttl_min: 120\n"
            "  providers:\n    g:\n      type: gemini\n      api_key: k\n"
            "  tasks:\n    t:\n      provider: g\n      model: m\n",
            encoding="utf-8",
        )
        cfg = load_config(cfg_path)
        assert cfg.ai.provider_ttl_min == 120

    def test_provider_ttl_min_default(self, tmp_config):
        cfg = load_config(tmp_config / "config.yaml")
        assert cfg.ai.provider_ttl_min == 60

    def test_provider_models_list(self, tmp_config):
        cfg = load_config(tmp_config / "config.yaml")
        g = cfg.ai.providers.get("gemini")
        assert g is not None
        assert g.models == ["gemini-2.5-flash", "gemini-2.0-flash"]

    def test_provider_models_defaults_to_empty(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "proxy:\n  enabled: false\n"
            "ai:\n  providers:\n    g:\n      type: gemini\n      api_key: k\n"
            "  tasks:\n    t:\n      provider: g\n      model: m\n",
            encoding="utf-8",
        )
        cfg = load_config(cfg_path)
        assert cfg.ai.providers["g"].models == []

    def test_with_project_dir_no_project_yaml(self, tmp_config):
        """project_dir without project.yaml returns base config."""
        cfg = load_config(tmp_config / "config.yaml", project_dir=tmp_config)
        assert cfg.compress.fps == 15

    def test_with_project_dir_and_project_yaml(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "compress:\n  fps: 15\n  target_size_mb: 5\nproxy:\n  enabled: false\n",
            encoding="utf-8",
        )
        proj_yaml = tmp_path / "project.yaml"
        proj_yaml.write_text("compress:\n  fps: 30\n", encoding="utf-8")

        cfg = load_config(cfg_path, project_dir=tmp_path)
        assert cfg.compress.fps == 15  # global-only field, project override ignored
        assert cfg.compress.target_size_mb == 5  # inherited

    def test_project_context_file_resolved_correctly(self, tmp_path):
        """context_file in project.yaml resolves relative to project_dir."""
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "ai:\n  providers:\n    g:\n      type: gemini\n      api_key: k\n"
            "  tasks:\n    t:\n      provider: g\n      model: m\n"
            "proxy:\n  enabled: false\n",
            encoding="utf-8",
        )
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        (proj_dir / "ctx.md").write_text("project specific", encoding="utf-8")
        (proj_dir / "project.yaml").write_text("ai:\n  context_file: ctx.md\n", encoding="utf-8")

        cfg = load_config(cfg_path, project_dir=proj_dir)
        assert cfg.ai.context == "project specific"


# ── WhisperConfig ────────────────────────────────────────────────────


class TestWhisperConfig:
    def test_defaults(self):
        cfg = WhisperConfig()
        assert cfg.enabled is True
        assert cfg.model_size == "medium"
        assert cfg.language == "zh"
        assert cfg.device == "auto"
        assert cfg.max_segments_per_clip == 5
        assert cfg.cache_dir == ""
        assert cfg.transcripts_subdir == "transcripts"

    def test_custom_values(self):
        cfg = WhisperConfig(enabled=True, model_size="small", language="en", device="cpu")
        assert cfg.enabled is True
        assert cfg.model_size == "small"
        assert cfg.language == "en"
        assert cfg.device == "cpu"

    def test_invalid_model_size_raises(self):
        with pytest.raises(ValueError):
            WhisperConfig(model_size="invalid").sanitize()

    def test_invalid_language_raises(self):
        with pytest.raises(ValueError):
            WhisperConfig(language="fr").sanitize()

    def test_invalid_device_raises(self):
        with pytest.raises(ValueError):
            WhisperConfig(device="gpu").sanitize()

    def test_zero_clips_raises(self):
        with pytest.raises(ValueError):
            cfg = WhisperConfig(max_segments_per_clip=0)
            cfg.sanitize()

    def test_auto_language_accepted(self):
        cfg = WhisperConfig(language="auto")
        cfg.sanitize()
        assert cfg.language == "auto"


# ── _load_dotenv ────────────────────────────────────────────────────


class TestLoadDotenv:
    def test_override_false_skips_existing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EXISTING_KEY", "old")
        (tmp_path / ".env").write_text("EXISTING_KEY=new\n", encoding="utf-8")
        from clio.config.loader import _load_dotenv

        _load_dotenv(tmp_path, override=False)
        assert os.environ["EXISTING_KEY"] == "old"

    def test_override_true_replaces_existing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EXISTING_KEY", "old")
        (tmp_path / ".env").write_text("EXISTING_KEY=new\n", encoding="utf-8")
        from clio.config.loader import _load_dotenv

        _load_dotenv(tmp_path, override=True)
        assert os.environ["EXISTING_KEY"] == "new"

    def test_override_sets_new_keys(self, tmp_path):
        (tmp_path / ".env").write_text("NEW_KEY=val\n", encoding="utf-8")
        from clio.config.loader import _load_dotenv

        _load_dotenv(tmp_path, override=True)
        assert os.environ["NEW_KEY"] == "val"
        del os.environ["NEW_KEY"]


# ── Helpers ─────────────────────────────────────────────────────────


def create_global_ai(
    providers: dict[str, str] | None = None,
) -> GlobalAIConfig:
    """Helper to build a GlobalAIConfig with provider data class objects."""
    ai = GlobalAIConfig()
    if providers:
        for name, ptype in providers.items():
            ai.providers[name] = ProviderConfig(name=name, type=ptype, api_key="k")
    return ai


def test_validate_global_rejects_bad_provider_type():
    import pytest

    from clio.config.models import GlobalAIConfig, GlobalConfig, ProviderConfig
    from clio.config.validators import validate_global_config

    gc = GlobalConfig(ai=GlobalAIConfig(providers={"x": ProviderConfig(name="x", type="nope")}))
    with pytest.raises(ValueError, match="type"):
        validate_global_config(gc)


def test_validate_global_rejects_zero_timeout():
    from clio.config.models import GlobalAIConfig, GlobalConfig, ProviderConfig
    from clio.config.validators import validate_global_config

    gc = GlobalConfig(
        ai=GlobalAIConfig(
            providers={"g": ProviderConfig(name="g", type="gemini", timeout_sec=0)},
            provider_ttl_min=0,
        )
    )
    with pytest.raises(ValueError, match="timeout_sec"):
        validate_global_config(gc)


def test_preview_subtitles_defaults():
    from clio.config.models import PreviewConfig

    pc = PreviewConfig()
    s = pc.subtitles
    assert s.enabled is True
    assert s.mode == "auto"
    assert s.max_lines == 2
    assert s.max_len_per_line == 16
    assert s.min_font_size == 14
    assert s.scroll_speed == 40
    assert s.font_size == 22
    assert s.font_family == ""
    assert s.font_color == "#ffffff"
    assert s.background == "rgba(0,0,0,0.55)"
    assert s.outline == "0 0 2px rgba(0,0,0,0.8)"
    assert s.pos_x == 50
    assert s.pos_y == 8


def test_validate_preview_subtitles_mode():
    from clio.config.models import PreviewConfig, PreviewSubtitlesConfig

    cfg = AppConfig(
        global_cfg=GlobalConfig(),
        project_cfg=ProjectConfig(
            preview=PreviewConfig(subtitles=PreviewSubtitlesConfig(mode="bad")),
        ),
    )
    with pytest.raises(ValueError, match="preview.subtitles.mode"):
        _validate_config(cfg)


def test_validate_preview_subtitles_pos_range():
    from clio.config.models import PreviewConfig, PreviewSubtitlesConfig

    cfg = AppConfig(
        global_cfg=GlobalConfig(),
        project_cfg=ProjectConfig(
            preview=PreviewConfig(subtitles=PreviewSubtitlesConfig(pos_x=150)),
        ),
    )
    with pytest.raises(ValueError, match="preview.subtitles.pos_x"):
        _validate_config(cfg)

    ok = AppConfig(
        global_cfg=GlobalConfig(),
        project_cfg=ProjectConfig(
            preview=PreviewConfig(subtitles=PreviewSubtitlesConfig(pos_x=50, pos_y=8)),
        ),
    )
    assert _validate_config(ok) is None
