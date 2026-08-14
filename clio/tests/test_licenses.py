"""GAP-P2-15: license metadata + third-party notice guard.

Verifies the Python metadata declares a license, that the third-party notice
exists and covers every declared runtime dependency, and that the desktop
bundling step ships LICENSE + THIRD_PARTY.md in every release path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Python 3.10 (local dev)
    import tomli as tomllib  # type: ignore[no-redef]

_REPO = Path(__file__).resolve().parents[2]
_PYPROJECT = _REPO / "pyproject.toml"
_REQ_RUNTIME = _REPO / "requirements.txt"


@pytest.fixture(scope="module")
def pyproject() -> dict:
    with _PYPROJECT.open("rb") as f:
        return tomllib.load(f)


def test_declares_license(pyproject: dict) -> None:
    project = pyproject["project"]
    license_value = project.get("license")
    assert license_value, "pyproject.toml [project] must declare a license"
    assert str(license_value).startswith("MIT")


def test_license_files_declared(pyproject: dict) -> None:
    files = pyproject["project"].get("license-files", [])
    assert "LICENSE" in files
    assert "THIRD_PARTY.md" in files


def test_license_files_exist() -> None:
    assert (_REPO / "LICENSE").is_file()
    assert (_REPO / "THIRD_PARTY.md").is_file()


def test_runtime_deps_covered_in_notice(pyproject: dict) -> None:
    """Every pinned direct runtime dependency must appear in THIRD_PARTY.md."""
    notice = (_REPO / "THIRD_PARTY.md").read_text(encoding="utf-8").lower()
    deps = pyproject["project"]["dependencies"]
    for dep in deps:
        name = dep.split("[", 1)[0].strip().split(">=", 1)[0].split("==", 1)[0].strip()
        assert name.lower() in notice, f"{name} missing from THIRD_PARTY.md"


def test_requirements_runtime_covered_in_notice() -> None:
    notice = (_REPO / "THIRD_PARTY.md").read_text(encoding="utf-8").lower()
    for raw in _REQ_RUNTIME.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split("[", 1)[0].strip().split(">=", 1)[0].split("==", 1)[0]
        assert name.lower() in notice, f"{name} missing from THIRD_PARTY.md"


def test_release_bundles_license_everywhere() -> None:
    """Every desktop release path must ship LICENSE + THIRD_PARTY.md."""
    release = (_REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    spec = (_REPO / "packaging" / "clio.spec").read_text(encoding="utf-8")
    ps1 = (_REPO / "packaging" / "build-desktop.ps1").read_text(encoding="utf-8")
    for haystack, label in ((release, "release.yml"), (spec, "clio.spec"), (ps1, "build-desktop.ps1")):
        assert "LICENSE" in haystack, f"{label} does not ship LICENSE"
        assert "THIRD_PARTY.md" in haystack, f"{label} does not ship THIRD_PARTY.md"
