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
    collect_doctor_checks,
    doctor_exit_code,
    is_virtualenv_python,
    node_version_supported,
    parse_node_major,
    parse_node_version,
)


def _config(
    tmp_path: Path,
    *,
    provider: ProviderConfig | None = None,
    whisper: ProjectWhisperConfig | None = None,
) -> AppConfig:
    provider = provider or ProviderConfig(
        name="gemini",
        type="gemini",
        api_key_env="GEMINI_API_KEY",
    )
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
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
