from __future__ import annotations

from pathlib import Path

import yaml

from clio.config import load_config
from clio.config.loader import _migrate_if_needed, _migrate_v1_to_v2, apply_run_paths
from clio.config.models import (
    AnalyzeConfig,
    AppConfig,
    CombinedAIConfig,
    CombinedCompressConfig,
    CombinedPaths,
    CombinedWhisperConfig,
    GlobalAIConfig,
    GlobalCompressConfig,
    GlobalConfig,
    GlobalPathsConfig,
    GlobalWhisperConfig,
    NamingConfig,
    PlanConfig,
    ProjectAIConfig,
    ProjectCompressConfig,
    ProjectConfig,
    ProjectPathsConfig,
    ProjectWhisperConfig,
    ProviderConfig,
    ProxyConfig,
    ScriptConfig,
    ServerConfig,
    TaskConfig,
)
from clio.ui.routes.config_routes import _validate_no_foreign_fields


class TestV1ToV2Migration:
    def test_migrates_v1_to_split(self, tmp_path: Path):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            yaml.dump(
                {
                    "paths": {"input_dir": "./in", "output_dir": "./out", "logs_dir": "./logs"},
                    "proxy": {"enabled": False},
                    "ai": {
                        "providers": {"g": {"type": "gemini", "api_key": "k"}},
                        "tasks": {"t": {"provider": "g", "model": "m"}},
                        "context": "hello",
                    },
                    "compress": {"target_size_mb": 10, "fps": 30},
                }
            ),
            encoding="utf-8",
        )

        _migrate_v1_to_v2(cfg_path)

        global_raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        assert global_raw.get("config_version") == "V2"
        assert "input_dir" not in global_raw.get("paths", {})
        assert "output_dir" not in global_raw.get("paths", {})
        assert global_raw["paths"]["logs_dir"] == "./logs"
        assert global_raw["proxy"]["enabled"] is False
        assert "tasks" not in global_raw.get("ai", {})
        assert "context" not in global_raw.get("ai", {})
        assert global_raw["compress"]["fps"] == 30
        assert "target_size_mb" not in global_raw.get("compress", {})

        proj_path = tmp_path / "project.yaml"
        assert proj_path.is_file()
        proj_raw = yaml.safe_load(proj_path.read_text(encoding="utf-8"))
        # input_dir/recursive are no longer project config fields
        assert "input_dir" not in proj_raw.get("paths", {})
        assert "recursive" not in proj_raw.get("paths", {})
        assert proj_raw["paths"]["output_dir"] == "./out"
        assert proj_raw["ai"]["tasks"]["t"]["provider"] == "g"
        assert proj_raw["ai"]["context"] == "hello"
        assert proj_raw["compress"]["target_size_mb"] == 10
        assert "fps" not in proj_raw.get("compress", {})

    def test_backup_created(self, tmp_path: Path):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("proxy:\n  enabled: false\n", encoding="utf-8")
        _migrate_v1_to_v2(cfg_path)
        bak = cfg_path.with_suffix(".yaml.bak")
        assert bak.is_file()

    def test_already_v2_skips(self, tmp_path: Path):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("config_version: V2\nproxy:\n  enabled: false\n", encoding="utf-8")
        _migrate_if_needed(cfg_path)
        # No .bak should exist
        bak = cfg_path.with_suffix(".yaml.bak")
        assert not bak.is_file()

    def test_v1_config_loaded_correctly_after_migration(self, tmp_path: Path):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            yaml.dump(
                {
                    "paths": {"input_dir": ".", "output_dir": "./output"},
                    "proxy": {"enabled": False},
                    "ai": {
                        "providers": {"g": {"type": "gemini", "api_key": "k"}},
                        "tasks": {"t": {"provider": "g", "model": "m"}},
                    },
                    "compress": {"target_size_mb": 5, "fps": 15},
                }
            ),
            encoding="utf-8",
        )

        cfg = load_config(cfg_path, project_dir=tmp_path)
        assert cfg.proxy.enabled is False
        assert cfg.compress.fps == 15
        assert cfg.compress.target_size_mb == 5
        assert "g" in cfg.ai.providers
        assert "t" in cfg.ai.tasks

    def test_project_yaml_override_in_loaded_config(self, tmp_path: Path):
        proj_path = tmp_path / "project.yaml"
        proj_path.write_text(
            yaml.dump(
                {
                    "compress": {"target_size_mb": 99},
                }
            ),
            encoding="utf-8",
        )
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            yaml.dump(
                {
                    "proxy": {"enabled": False},
                    "compress": {"fps": 15, "target_size_mb": 5},
                }
            ),
            encoding="utf-8",
        )

        cfg = load_config(cfg_path, project_dir=tmp_path)
        assert cfg.compress.fps == 15
        assert cfg.compress.target_size_mb == 99

    def test_legacy_split_keys_are_ignored_without_reinjection(self, tmp_path: Path):
        project_path = tmp_path / "project.yaml"
        project_path.write_text(
            yaml.dump(
                {
                    "compress": {
                        "target_size_mb": 8,
                        "split_max_min": 15,
                        "splits_subdir": "splits",
                        "reencode_split": True,
                    }
                }
            ),
            encoding="utf-8",
        )
        config_path = tmp_path / "config.yaml"
        config_path.write_text("config_version: V2\n", encoding="utf-8")

        config = load_config(config_path, project_dir=tmp_path)

        assert config.compress.target_size_mb == 8
        assert not hasattr(config.compress, "split_max_min")

        fresh_project = tmp_path / "fresh"
        fresh_project.mkdir()
        fresh_project_yaml = fresh_project / "project.yaml"
        fresh_project_yaml.write_text("compress:\n  target_size_mb: 6\n", encoding="utf-8")
        load_config(config_path, project_dir=fresh_project)
        fresh_raw = yaml.safe_load(fresh_project_yaml.read_text(encoding="utf-8"))
        assert "split_max_min" not in fresh_raw["compress"]
        assert "splits_subdir" not in fresh_raw["compress"]
        assert "reencode_split" not in fresh_raw["compress"]

    def test_project_yaml_relative_paths_resolve_from_project_dir(self, tmp_path: Path):
        app_dir = tmp_path / "app"
        project_dir = tmp_path / "projects" / "trip"
        app_dir.mkdir()
        project_dir.mkdir(parents=True)
        cfg_path = app_dir / "config.yaml"
        cfg_path.write_text(
            yaml.dump(
                {
                    "config_version": "V2",
                    "proxy": {"enabled": False},
                    "ai": {
                        "providers": {
                            "deepseek": {
                                "type": "openai",
                                "api_key": "k",
                                "base_url": "https://api.deepseek.com/v1",
                            }
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        (project_dir / "project.yaml").write_text(
            yaml.dump(
                {
                    "paths": {"output_dir": "./output"},
                    "ai": {"tasks": {"voiceover": {"provider": "deepseek", "model": "deepseek-chat"}}},
                    "script": {"template_file": "./templates/vlog_template.md"},
                }
            ),
            encoding="utf-8",
        )

        cfg = load_config(cfg_path, project_dir=project_dir)

        assert cfg.paths.output_dir == (project_dir / "output").resolve()
        assert cfg.script.template_file == (project_dir / "templates" / "vlog_template.md").resolve()

    def test_v2_config_without_project_yaml_uses_defaults(self, tmp_path: Path):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            yaml.dump(
                {
                    "config_version": "V2",
                    "proxy": {"enabled": False},
                }
            ),
            encoding="utf-8",
        )
        cfg = load_config(cfg_path, project_dir=tmp_path)
        assert cfg.compress.target_size_mb == 5
        assert cfg.analyze.skip_existing is True
        assert cfg.ai.debug_print_prompt is False


class TestCombinedWrappers:
    def test_combined_paths_global_fields(self):
        g = GlobalPathsConfig(ffmpeg="/usr/bin/ffmpeg", ffprobe="/usr/bin/ffprobe", logs_dir=Path("./my_logs"))
        p = ProjectPathsConfig(output_dir=Path("./out"))
        c = CombinedPaths(g, p)
        assert c.ffmpeg == "/usr/bin/ffmpeg"
        assert c.ffprobe == "/usr/bin/ffprobe"
        assert c.logs_dir == Path("./my_logs")
        assert c.output_dir == Path("./out")

    def test_combined_paths_no_project(self):
        g = GlobalPathsConfig(ffmpeg="ffmpeg")
        c = CombinedPaths(g, None)
        assert c.ffmpeg == "ffmpeg"

    def test_combined_ai_delegation(self):
        g = GlobalAIConfig(
            providers={"g": ProviderConfig(name="g", type="gemini", api_key="k")},
            debug_print_prompt=True,
            provider_ttl_min=120,
        )
        p = ProjectAIConfig(
            tasks={"t": TaskConfig(provider="g", model="m")},
            context="project context",
        )
        c = CombinedAIConfig(g, p)
        assert "g" in c.providers
        assert c.debug_print_prompt is True
        assert c.provider_ttl_min == 120
        assert "t" in c.tasks
        assert c.context == "project context"

    def test_combined_ai_no_project(self):
        g = GlobalAIConfig(providers={"g": ProviderConfig(name="g", type="gemini", api_key="k")})
        c = CombinedAIConfig(g, None)
        assert "g" in c.providers
        assert c.tasks == {}
        assert c.context == ""

    def test_global_default_tasks_apply_to_project_without_overrides(self):
        g = GlobalAIConfig(
            default_tasks={
                "video_analyze": TaskConfig(provider="gemini", model="gemini-2.5-flash"),
                "voiceover": TaskConfig(provider="deepseek", model="deepseek-chat"),
            },
        )
        c = CombinedAIConfig(g, ProjectAIConfig(tasks={}))
        assert c.tasks["video_analyze"].model == "gemini-2.5-flash"
        assert c.tasks["voiceover"].provider == "deepseek"
        assert c.tasks["refine_text"] == c.tasks["video_analyze"]

    def test_project_task_overrides_global_default(self):
        g = GlobalAIConfig(
            default_tasks={
                "video_analyze": TaskConfig(provider="gemini", model="gemini-2.5-flash"),
                "voiceover": TaskConfig(provider="deepseek", model="deepseek-chat"),
            },
        )
        p = ProjectAIConfig(tasks={"voiceover": TaskConfig(provider="deepseek", model="deepseek-reasoner")})
        c = CombinedAIConfig(g, p)
        assert c.tasks["video_analyze"].model == "gemini-2.5-flash"
        assert c.tasks["voiceover"].model == "deepseek-reasoner"

    def test_refine_text_follows_project_video_override(self):
        g = GlobalAIConfig(
            default_tasks={
                "video_analyze": TaskConfig(provider="gemini", model="gemini-2.5-flash"),
                "refine_text": TaskConfig(provider="deepseek", model="deepseek-chat"),
            },
        )
        p = ProjectAIConfig(tasks={"video_analyze": TaskConfig(provider="gemini", model="gemini-3-flash")})
        c = CombinedAIConfig(g, p)
        assert c.tasks["refine_text"] == c.tasks["video_analyze"]

    def test_combined_compress_global_takes_fps_project_takes_target(self):
        g = GlobalCompressConfig(fps=30, codec="libx264", remove_audio=True, crf=23)
        p = ProjectCompressConfig(target_size_mb=10, max_width=1280)
        c = CombinedCompressConfig(g, p)
        assert c.fps == 30
        assert c.codec == "libx264"
        assert c.remove_audio is True
        assert c.crf == 23
        assert c.target_size_mb == 10
        assert c.max_width == 1280

    def test_combined_compress_no_project(self):
        g = GlobalCompressConfig(fps=15)
        c = CombinedCompressConfig(g, None)
        assert c.fps == 15
        assert c.target_size_mb == 5

    def test_combined_whisper_delegation(self):
        g = GlobalWhisperConfig(cache_dir="/cache", hf_endpoint="https://hf-mirror.com")
        p = ProjectWhisperConfig(enabled=True, model_size="large", language="en", device="cuda")
        c = CombinedWhisperConfig(g, p)
        assert c.cache_dir == "/cache"
        assert c.hf_endpoint == "https://hf-mirror.com"
        assert c.enabled is True
        assert c.model_size == "large"
        assert c.language == "en"
        assert c.device == "cuda"

    def test_combined_whisper_no_project(self):
        g = GlobalWhisperConfig()
        c = CombinedWhisperConfig(g, None)
        assert c.enabled is True
        assert c.model_size == "medium"

    def test_app_config_combined_properties(self):
        cfg = AppConfig(
            global_cfg=GlobalConfig(
                paths=GlobalPathsConfig(ffmpeg="ffmpeg"),
                compress=GlobalCompressConfig(fps=30),
                ai=GlobalAIConfig(providers={"g": ProviderConfig(name="g", type="gemini", api_key="k")}),
            ),
            project_cfg=ProjectConfig(
                paths=ProjectPathsConfig(output_dir=Path("./out")),
                compress=ProjectCompressConfig(target_size_mb=99),
                ai=ProjectAIConfig(tasks={"t": TaskConfig(provider="g", model="m")}),
            ),
        )
        assert cfg.paths.ffmpeg == "ffmpeg"
        assert cfg.paths.output_dir == Path("./out")
        assert cfg.compress.fps == 30
        assert cfg.compress.target_size_mb == 99
        assert "g" in cfg.ai.providers
        assert "t" in cfg.ai.tasks

    def test_app_config_no_project(self):
        cfg = AppConfig(global_cfg=GlobalConfig())
        assert cfg.compress.fps == 15
        assert cfg.compress.target_size_mb == 5

    def test_global_only_fields_not_affected_by_project(self):
        cfg = AppConfig(
            global_cfg=GlobalConfig(compress=GlobalCompressConfig(fps=15, codec="libx264")),
            project_cfg=ProjectConfig(compress=ProjectCompressConfig(target_size_mb=99)),
        )
        assert cfg.compress.fps == 15
        assert cfg.compress.codec == "libx264"
        assert cfg.compress.target_size_mb == 99

    def test_project_only_fields_are_readable(self):
        cfg = AppConfig(
            global_cfg=GlobalConfig(),
            project_cfg=ProjectConfig(
                analyze=AnalyzeConfig(skip_existing=False, max_workers=4),
                script=ScriptConfig(scripts_subdir="my_scripts"),
                plan=PlanConfig(plans_subdir="my_plans", max_clips_per_day=20),
            ),
        )
        assert cfg.analyze.max_workers == 4
        assert cfg.analyze.skip_existing is False
        assert cfg.script.scripts_subdir == "my_scripts"
        assert cfg.plan.plans_subdir == "my_plans"
        assert cfg.plan.max_clips_per_day == 20

    def test_global_only_sections_accessible(self):
        cfg = AppConfig(
            global_cfg=GlobalConfig(
                proxy=ProxyConfig(enabled=True, url="socks5://127.0.0.1:1080"),
                naming=NamingConfig(index_width=4),
                server=ServerConfig(api_token="abc"),
            ),
        )
        assert cfg.proxy.enabled is True
        assert cfg.proxy.url == "socks5://127.0.0.1:1080"
        assert cfg.naming.index_width == 4
        assert cfg.server.api_token == "abc"


class TestGlobalDefaultTasks:
    def test_loads_global_default_tasks_from_config_yaml(self, tmp_path: Path):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            yaml.dump(
                {
                    "ai": {
                        "providers": {
                            "gemini": {"type": "gemini", "api_key": "g"},
                            "deepseek": {
                                "type": "openai",
                                "api_key": "d",
                                "base_url": "https://api.deepseek.com/v1",
                            },
                        },
                        "default_tasks": {
                            "video_analyze": {"provider": "gemini", "model": "gemini-2.5-flash"},
                            "voiceover": {"provider": "deepseek", "model": "deepseek-chat"},
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

        cfg = load_config(cfg_path, project_dir=tmp_path)
        assert cfg.ai.tasks["video_analyze"].provider == "gemini"
        assert cfg.ai.tasks["voiceover"].model == "deepseek-chat"
        assert cfg.ai.tasks["refine_text"] == cfg.ai.tasks["video_analyze"]

    def test_upgrades_missing_default_task_fields(self, tmp_path: Path):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            yaml.dump(
                {
                    "ai": {
                        "providers": {"gemini": {"type": "gemini", "api_key": "g"}},
                        "default_tasks": {"video_analyze": {"provider": "gemini"}},
                    },
                }
            ),
            encoding="utf-8",
        )

        cfg = load_config(cfg_path, project_dir=tmp_path)
        assert cfg.ai.tasks["video_analyze"].model == ""
        upgraded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        assert upgraded["ai"]["default_tasks"]["video_analyze"]["model"] == ""


class TestProhibitedFields:
    def test_deprecated_split_field_rejected(self):
        err = _validate_no_foreign_fields({"compress": {"split_max_min": 15}}, "project")
        assert err is not None
        assert "已废弃" in err

    def test_project_section_in_global(self):
        err = _validate_no_foreign_fields({"analyze": {}}, "global")
        assert err is not None
        assert "analyze" in err

    def test_global_section_in_project(self):
        err = _validate_no_foreign_fields({"proxy": {}}, "project")
        assert err is not None
        assert "proxy" in err

    def test_project_field_in_global_split_section(self):
        err = _validate_no_foreign_fields({"compress": {"target_size_mb": 10}}, "global")
        assert err is not None
        assert "compress.target_size_mb" in err

    def test_global_field_in_project_split_section(self):
        err = _validate_no_foreign_fields({"compress": {"fps": 30}}, "project")
        assert err is not None
        assert "compress.fps" in err

    def test_global_default_tasks_rejected_in_project(self):
        err = _validate_no_foreign_fields(
            {"ai": {"default_tasks": {"voiceover": {"provider": "deepseek", "model": "deepseek-chat"}}}},
            "project",
        )
        assert err is not None
        assert "ai.default_tasks" in err

    def test_global_default_tasks_allowed_in_global(self):
        err = _validate_no_foreign_fields(
            {"ai": {"default_tasks": {"voiceover": {"provider": "deepseek", "model": "deepseek-chat"}}}},
            "global",
        )
        assert err is None

    def test_project_field_in_project_split_section_passes(self):
        err = _validate_no_foreign_fields({"compress": {"target_size_mb": 10}}, "project")
        assert err is None

    def test_global_field_in_global_split_section_passes(self):
        err = _validate_no_foreign_fields({"compress": {"fps": 30}}, "global")
        assert err is None

    def test_split_section_allowed_in_both_layers(self):
        err_g = _validate_no_foreign_fields({"compress": {"fps": 30}}, "global")
        err_p = _validate_no_foreign_fields({"compress": {"target_size_mb": 10}}, "project")
        assert err_g is None
        assert err_p is None

    def test_mixed_fields_in_split_section_rejected(self):
        err = _validate_no_foreign_fields({"compress": {"fps": 30, "target_size_mb": 10}}, "project")
        assert err is not None
        assert "compress.fps" in err

    def test_export_section_rejected_in_global(self):
        err = _validate_no_foreign_fields({"export": {}}, "global")
        assert err is not None
        assert "export" in err

    def test_proxy_section_rejected_in_project(self):
        err = _validate_no_foreign_fields({"proxy": {}}, "project")
        assert err is not None
        assert "proxy" in err

    def test_unknown_section_allowed(self):
        err = _validate_no_foreign_fields({"custom_section": {"key": "val"}}, "global")
        assert err is None


class TestApplyRunPaths:
    def test_output_dir_set_explicitly(self):
        cfg = AppConfig(
            global_cfg=GlobalConfig(),
            project_cfg=ProjectConfig(paths=ProjectPathsConfig(output_dir=Path("out"))),
        )
        new_output = (Path.cwd() / "custom_output").resolve()
        result = apply_run_paths(cfg, output_dir=new_output)
        assert result.project_cfg.paths.output_dir == new_output

    def test_no_project_cfg_returns_unchanged(self):
        cfg = AppConfig(global_cfg=GlobalConfig())
        result = apply_run_paths(cfg)
        assert result.project_cfg is None

    def test_returns_deep_copy(self):
        cfg = AppConfig(
            global_cfg=GlobalConfig(),
            project_cfg=ProjectConfig(paths=ProjectPathsConfig(output_dir=Path("out"))),
        )
        result = apply_run_paths(cfg, output_dir=Path("/custom_output"))
        assert result is not cfg
        assert result.project_cfg is not cfg.project_cfg
        assert result.project_cfg.paths is not cfg.project_cfg.paths
