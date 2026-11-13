from __future__ import annotations

from pathlib import Path

from clio.config.models import (
    AppConfig,
    GlobalAIConfig,
    GlobalConfig,
    GlobalPathsConfig,
    ProjectAIConfig,
    ProjectConfig,
    ProjectPathsConfig,
    ProjectWhisperConfig,
    ProviderConfig,
    TaskConfig,
)
from clio.doctor import (
    DoctorItem,
    _print_doctor_items,
    collect_doctor_checks,
    doctor_exit_code,
    is_virtualenv_python,
    node_version_supported,
    parse_node_major,
    parse_node_version,
    provider_key_missing,
    run_doctor,
)
from clio.i18n import t


def _config(
    tmp_path: Path,
    *,
    provider: ProviderConfig | None = None,
    whisper: ProjectWhisperConfig | None = None,
    project_dir: Path | None = None,
) -> AppConfig:
    provider = provider or ProviderConfig(
        name="gemini",
        type="gemini",
        api_key_env="GEMINI_API_KEY",
    )
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    input_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)
    return AppConfig(
        global_cfg=GlobalConfig(
            paths=GlobalPathsConfig(ffmpeg="", ffprobe=""),
            ai=GlobalAIConfig(providers={provider.name: provider}),
        ),
        project_cfg=ProjectConfig(
            paths=ProjectPathsConfig(output_dir=output_dir),
            ai=ProjectAIConfig(tasks={"video_analyze": TaskConfig(provider=provider.name, model="m")}),
            whisper=whisper or ProjectWhisperConfig(),
        ),
        project_dir=project_dir,
    )


def test_doctor_exit_code_fails_on_failures() -> None:
    assert doctor_exit_code([DoctorItem("x", "FAIL", "broken")]) == 1


def test_doctor_exit_code_ignores_warnings() -> None:
    assert doctor_exit_code([DoctorItem("x", "WARN", "heads up")]) == 0


def test_parse_node_major() -> None:
    assert parse_node_major("v22.4.1") == 22
    assert parse_node_major("16.14.0") == 16
    assert parse_node_major("not node") is None


def test_parse_node_version() -> None:
    assert parse_node_version("v22.12.0") == (22, 12, 0)
    assert parse_node_version("24.1") == (24, 1, 0)
    assert parse_node_version("not node") is None


def test_node_version_supported_requires_22_12() -> None:
    assert node_version_supported("v22.12.0")
    assert node_version_supported("v24.0.0")
    assert not node_version_supported("v22.11.0")
    assert not node_version_supported("v20.19.0")
    assert not node_version_supported(None)


def test_is_virtualenv_python_accepts_prefix_difference() -> None:
    assert is_virtualenv_python("/usr/bin/python", prefix="/tmp/project/.venv", base_prefix="/usr")


def test_is_virtualenv_python_accepts_linux_venv_bin() -> None:
    assert is_virtualenv_python("/work/project/.venv/bin/python", prefix="/usr", base_prefix="/usr")


def test_is_virtualenv_python_accepts_windows_venv_scripts() -> None:
    assert is_virtualenv_python(
        r"C:\work\project\.venv\Scripts\python.exe", prefix=r"C:\Python311", base_prefix=r"C:\Python311"
    )


def test_is_virtualenv_python_accepts_pyvenv_cfg(tmp_path: Path) -> None:
    env_dir = tmp_path / "custom-env"
    bin_dir = env_dir / "bin"
    bin_dir.mkdir(parents=True)
    (env_dir / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")

    assert is_virtualenv_python(bin_dir / "python", prefix="/usr", base_prefix="/usr")


def test_is_virtualenv_python_rejects_system_python() -> None:
    assert not is_virtualenv_python("/usr/bin/python", prefix="/usr", base_prefix="/usr")


def test_collect_checks_reports_missing_task_provider_key(tmp_path: Path) -> None:
    config = _config(tmp_path)
    items = collect_doctor_checks(
        config,
        discover_binary=lambda name: f"/usr/bin/{name}",
        environ={},
        node_version_getter=lambda: "v22.0.0",
    )

    provider_item = next(item for item in items if item.name == "AI provider: gemini")
    assert provider_item.status == "FAIL"
    assert "GEMINI_API_KEY" in provider_item.detail


def test_collect_checks_accepts_env_provider_key(tmp_path: Path) -> None:
    config = _config(tmp_path)
    items = collect_doctor_checks(
        config,
        discover_binary=lambda name: f"/usr/bin/{name}",
        environ={"GEMINI_API_KEY": "secret"},
        node_version_getter=lambda: "v22.0.0",
    )

    provider_item = next(item for item in items if item.name == "AI provider: gemini")
    assert provider_item.status == "OK"


def test_collect_checks_uses_english_locale(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLIO_LANG", "en")
    config = _config(tmp_path)

    items = collect_doctor_checks(
        config,
        discover_binary=lambda name: None,
        environ={},
        node_version_getter=lambda: None,
    )

    assert any(item.name == "Virtual environment" for item in items)
    provider_item = next(item for item in items if item.name == "AI provider: gemini")
    assert "Missing environment variable GEMINI_API_KEY" in provider_item.detail
    assert next(item for item in items if item.name == "ffmpeg").detail == (
        "Not found; run the setup script or configure the path in config.yaml"
    )
    assert next(item for item in items if item.name == "Node.js").detail == (
        "Not found; Node.js 22.12+ is only needed for UI unit tests"
    )


def test_doctor_output_header_uses_english_locale(monkeypatch, capsys) -> None:
    monkeypatch.setenv("CLIO_LANG", "en")

    _print_doctor_items([DoctorItem("Python", "OK")])

    output = capsys.readouterr().out
    assert "Environment diagnosis:" in output
    assert "done (exit code: 0)" in output


def test_collect_checks_warns_for_old_node(tmp_path: Path) -> None:
    config = _config(tmp_path, provider=ProviderConfig(name="gemini", type="gemini", api_key="secret"))
    items = collect_doctor_checks(
        config,
        discover_binary=lambda name: f"/usr/bin/{name}",
        environ={},
        node_version_getter=lambda: "v16.14.0",
    )

    node_item = next(item for item in items if item.name == "Node.js")
    assert node_item.status == "WARN"
    assert "22.12+" in node_item.detail


def test_collect_checks_accepts_node_22_12(tmp_path: Path) -> None:
    config = _config(tmp_path, provider=ProviderConfig(name="gemini", type="gemini", api_key="secret"))
    items = collect_doctor_checks(
        config,
        discover_binary=lambda name: f"/usr/bin/{name}",
        environ={},
        node_version_getter=lambda: "v22.12.0",
    )

    node_item = next(item for item in items if item.name == "Node.js")
    assert node_item.status == "OK"


def test_collect_checks_warns_for_node_22_before_minimum(tmp_path: Path) -> None:
    config = _config(tmp_path, provider=ProviderConfig(name="gemini", type="gemini", api_key="secret"))
    items = collect_doctor_checks(
        config,
        discover_binary=lambda name: f"/usr/bin/{name}",
        environ={},
        node_version_getter=lambda: "v22.11.0",
    )

    node_item = next(item for item in items if item.name == "Node.js")
    assert node_item.status == "WARN"


def test_collect_checks_reports_missing_cloud_asr_key(tmp_path: Path) -> None:
    config = _config(tmp_path, whisper=ProjectWhisperConfig(engine="aliyun"))
    items = collect_doctor_checks(
        config,
        discover_binary=lambda name: f"/usr/bin/{name}",
        environ={},
        node_version_getter=lambda: "v22.0.0",
    )

    asr_item = next(item for item in items if item.name.startswith("ASR:"))
    assert asr_item.status == "FAIL"
    assert "DASHSCOPE_API_KEY" in asr_item.detail


def test_collect_checks_accepts_cloud_asr_key_without_local_whisper(tmp_path: Path) -> None:
    config = _config(tmp_path, whisper=ProjectWhisperConfig(engine="aliyun"))
    items = collect_doctor_checks(
        config,
        discover_binary=lambda name: f"/usr/bin/{name}",
        environ={"DASHSCOPE_API_KEY": "cloud-key"},
        node_version_getter=lambda: "v22.0.0",
    )

    asr_item = next(item for item in items if item.name.startswith("ASR:"))
    assert asr_item.status == "OK"


def test_provider_key_detail_without_env_name() -> None:
    provider = ProviderConfig(name="custom", type="openai", api_key="")

    assert provider_key_missing(provider, {}) == t("doctor.provider.missing_env_name")


def test_ollama_provider_does_not_require_api_key() -> None:
    provider = ProviderConfig(name="ollama", type="ollama", api_key="")

    assert provider_key_missing(provider, {}) is None


def test_collect_checks_marks_disabled_asr_ok(tmp_path: Path) -> None:
    config = _config(tmp_path, whisper=ProjectWhisperConfig(enabled=False))

    items = collect_doctor_checks(
        config,
        discover_binary=lambda name: f"/usr/bin/{name}",
        environ={},
        node_version_getter=lambda: "v22.0.0",
    )

    asr_item = next(item for item in items if item.name == "ASR")
    assert asr_item.status == "OK"
    assert asr_item.detail == t("doctor.asr.disabled")


def test_collect_checks_reports_project_video_states(tmp_path: Path, monkeypatch) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    present = project_dir / "a.mp4"
    present.write_bytes(b"x")
    offline = project_dir / "b.mp4"
    config = _config(tmp_path, project_dir=project_dir)
    row_name = t("doctor.item.project_videos")

    monkeypatch.setattr("clio.tasks._video_loader.source_videos", lambda cfg: [])
    items = collect_doctor_checks(
        config, discover_binary=lambda name: "x", environ={}, node_version_getter=lambda: "v22.0.0"
    )
    empty = next(item for item in items if item.name == row_name)
    assert empty.status == "WARN"
    assert empty.detail == t("doctor.project.empty", {"path": project_dir})

    monkeypatch.setattr("clio.tasks._video_loader.source_videos", lambda cfg: [present, offline])
    items = collect_doctor_checks(
        config, discover_binary=lambda name: "x", environ={}, node_version_getter=lambda: "v22.0.0"
    )
    partial = next(item for item in items if item.name == row_name)
    assert partial.status == "WARN"
    assert partial.detail == t("doctor.project.offline", {"path": project_dir, "count": 2, "offline": 1})

    monkeypatch.setattr("clio.tasks._video_loader.source_videos", lambda cfg: [present])
    items = collect_doctor_checks(
        config, discover_binary=lambda name: "x", environ={}, node_version_getter=lambda: "v22.0.0"
    )
    ready = next(item for item in items if item.name == row_name)
    assert ready.status == "OK"
    assert ready.detail == t("doctor.project.ready", {"path": project_dir, "count": 1})


def test_collect_checks_flags_unconfigured_referenced_provider(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    config = AppConfig(
        global_cfg=GlobalConfig(paths=GlobalPathsConfig(ffmpeg="", ffprobe=""), ai=GlobalAIConfig(providers={})),
        project_cfg=ProjectConfig(
            paths=ProjectPathsConfig(output_dir=output_dir),
            ai=ProjectAIConfig(tasks={"video_analyze": TaskConfig(provider="ghost", model="m")}),
        ),
    )

    items = collect_doctor_checks(
        config, discover_binary=lambda name: "x", environ={}, node_version_getter=lambda: "v22.0.0"
    )

    ghost = next(item for item in items if item.name == "AI provider: ghost")
    assert ghost.status == "FAIL"
    assert ghost.detail == t("doctor.provider.unconfigured")


def _set_tasks(config: AppConfig, tasks: dict[str, TaskConfig]) -> None:
    assert config.project_cfg is not None
    config.project_cfg.ai.tasks = tasks


def _collect_checks(config: AppConfig, *, environ=None, connectivity_tester=None):
    return collect_doctor_checks(
        config,
        discover_binary=lambda name: "x",
        environ={} if environ is None else environ,
        node_version_getter=lambda: "v22.12.0",
        connectivity_tester=connectivity_tester,
    )


def test_collect_checks_warns_on_known_legacy_defaults(tmp_path: Path) -> None:
    provider = ProviderConfig(name="gemini", type="gemini", api_key="secret", models=["m"], max_tokens=4096)
    config = _config(tmp_path, provider=provider, whisper=ProjectWhisperConfig(enabled=False))
    config.global_cfg.compress.remove_audio = True
    config.global_cfg.ai.debug_print_prompt = True
    config.analyze.max_analyze_duration_min = 30

    items = _collect_checks(config)

    warning_names = {
        t("doctor.item.legacy_default", {"key": "compress.remove_audio"}),
        t("doctor.item.legacy_default", {"key": "ai.debug_print_prompt"}),
        t("doctor.item.legacy_default", {"key": "analyze.max_analyze_duration_min"}),
        t("doctor.item.legacy_default", {"key": "ai.providers.max_tokens"}),
    }
    warnings = [item for item in items if item.name in warning_names]
    assert len(warnings) == 4
    assert all(item.status == "WARN" for item in warnings)


def test_collect_checks_warns_when_binding_model_is_not_registered(tmp_path: Path) -> None:
    provider = ProviderConfig(name="gemini", type="gemini", api_key="secret", models=["allowed-model"])
    config = _config(tmp_path, provider=provider, whisper=ProjectWhisperConfig(enabled=False))
    _set_tasks(config, {"vlog_plan": TaskConfig(provider="gemini", model="missing-model")})

    items = _collect_checks(config)

    model_item = next(item for item in items if item.name == t("doctor.item.ai_model", {"task": "vlog_plan"}))
    assert model_item.status == "WARN"
    assert model_item.detail == t(
        "doctor.model.unlisted",
        {"model": "missing-model", "provider": "gemini", "available": "allowed-model"},
    )


def test_collect_checks_allows_unregistered_models_when_registry_is_empty(tmp_path: Path) -> None:
    calls: list[str] = []
    provider = ProviderConfig(name="custom", type="openai", api_key="secret", models=[])
    config = _config(tmp_path, provider=provider, whisper=ProjectWhisperConfig(enabled=False))
    _set_tasks(config, {"vlog_plan": TaskConfig(provider="custom", model="third-party-model")})

    def tester(probe_config, *, provider_name, model):
        calls.append(model)
        return {"ok": True, "elapsed_ms": 25}

    items = _collect_checks(config, connectivity_tester=tester)

    assert not any(item.name == t("doctor.item.ai_model", {"task": "vlog_plan"}) for item in items)
    binding_item = next(
        item
        for item in items
        if item.name == t("doctor.item.ai_binding", {"provider": "custom", "model": "third-party-model"})
    )
    assert binding_item.status == "OK"
    assert calls == ["third-party-model"]


def test_connectivity_probe_is_grouped_per_provider_model(tmp_path: Path) -> None:
    calls: list[str] = []
    provider = ProviderConfig(name="gemini", type="gemini", api_key="secret", models=["m"], timeout_sec=120)
    config = _config(tmp_path, provider=provider, whisper=ProjectWhisperConfig(enabled=False))
    _set_tasks(
        config,
        {
            "script_voiceover": TaskConfig(provider="gemini", model="m"),
            "vlog_plan": TaskConfig(provider="gemini", model="m"),
        },
    )

    def tester(probe_config, *, provider_name, model):
        calls.append(model)
        probe_provider = probe_config.ai.providers[provider_name]
        assert probe_provider.retry_attempts == 0
        assert probe_provider.timeout_sec == 20.0
        assert config.ai.providers[provider_name].retry_attempts == 2
        return {"ok": True, "elapsed_ms": 42}

    items = _collect_checks(config, connectivity_tester=tester)

    binding_item = next(
        item for item in items if item.name == t("doctor.item.ai_binding", {"provider": "gemini", "model": "m"})
    )
    assert binding_item.status == "OK"
    assert "script_voiceover" in binding_item.detail
    assert "vlog_plan" in binding_item.detail
    assert calls == ["m"]


def test_connectivity_probe_skips_missing_key_and_empty_model(tmp_path: Path) -> None:
    calls: list[str] = []
    provider = ProviderConfig(
        name="gemini",
        type="gemini",
        api_key_env="GEMINI_API_KEY",
        models=["m"],
    )
    config = _config(tmp_path, provider=provider, whisper=ProjectWhisperConfig(enabled=False))
    _set_tasks(config, {"vlog_plan": TaskConfig(provider="gemini", model="")})

    def tester(probe_config, *, provider_name, model):
        calls.append(model)
        return {"ok": True}

    items = _collect_checks(config, environ={}, connectivity_tester=tester)

    assert calls == []
    model_item = next(item for item in items if item.name == t("doctor.item.ai_model", {"task": "vlog_plan"}))
    assert model_item.status == "WARN"
    assert model_item.detail == t("doctor.model.empty")


def test_connectivity_probe_runs_for_ollama_without_api_key(tmp_path: Path) -> None:
    calls: list[str] = []
    provider = ProviderConfig(
        name="ollama",
        type="ollama",
        base_url="http://localhost:11434/v1",
        models=["llama3.2"],
    )
    config = _config(tmp_path, provider=provider, whisper=ProjectWhisperConfig(enabled=False))
    _set_tasks(config, {"vlog_plan": TaskConfig(provider="ollama", model="llama3.2")})

    def tester(probe_config, *, provider_name, model):
        calls.append(model)
        return {"ok": True, "elapsed_ms": 10}

    items = _collect_checks(config, environ={}, connectivity_tester=tester)

    provider_item = next(item for item in items if item.name == "AI provider: ollama")
    binding_item = next(
        item for item in items if item.name == t("doctor.item.ai_binding", {"provider": "ollama", "model": "llama3.2"})
    )
    assert provider_item.status == "OK"
    assert binding_item.status == "OK"
    assert calls == ["llama3.2"]
    assert doctor_exit_code(items) == 0


def test_connectivity_probe_failures_only_warn(tmp_path: Path) -> None:
    provider = ProviderConfig(name="gemini", type="gemini", api_key="secret", models=["m"])
    config = _config(tmp_path, provider=provider, whisper=ProjectWhisperConfig(enabled=False))
    _set_tasks(config, {"vlog_plan": TaskConfig(provider="gemini", model="m")})

    def failed_tester(probe_config, *, provider_name, model):
        return {"ok": False, "error": "model unavailable"}

    failed_items = _collect_checks(config, connectivity_tester=failed_tester)
    failed_item = next(
        item for item in failed_items if item.name == t("doctor.item.ai_binding", {"provider": "gemini", "model": "m"})
    )
    assert failed_item.status == "WARN"
    assert "model unavailable" in failed_item.detail
    assert doctor_exit_code(failed_items) == 0

    def crashing_tester(probe_config, *, provider_name, model):
        raise RuntimeError("connection exploded")

    crash_items = _collect_checks(config, connectivity_tester=crashing_tester)
    crash_item = next(
        item for item in crash_items if item.name == t("doctor.item.ai_binding", {"provider": "gemini", "model": "m"})
    )
    assert crash_item.status == "WARN"
    assert "connection exploded" in crash_item.detail
    assert doctor_exit_code(crash_items) == 0


def test_run_doctor_passes_project_dir_and_reports_config_failure(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("proxy:\n  enabled: false\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_load_config(path, project_dir=None):
        captured["project_dir"] = project_dir
        return _config(tmp_path)

    monkeypatch.setattr("clio.doctor.load_config", fake_load_config)

    def fake_collect(config, **kwargs):
        captured["connectivity_tester"] = kwargs.get("connectivity_tester")
        return []

    monkeypatch.setattr("clio.doctor.collect_doctor_checks", fake_collect)
    monkeypatch.setattr("clio.doctor._print_doctor_items", lambda items: None)
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    assert run_doctor(config_path, project_dir) == 0
    assert captured["project_dir"] == project_dir
    assert callable(captured["connectivity_tester"])

    def boom(*args, **kwargs):
        raise ValueError("bad config")

    monkeypatch.setattr("clio.doctor.load_config", boom)
    assert run_doctor(config_path, None) == 1
