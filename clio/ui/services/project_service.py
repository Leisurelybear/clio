"""Project management utilities for the UI server.

Closure functions extracted from server.py's make_handler(), now parameterized:
- _project_output_dir
- _detect_steps
- _registry_path
- _add_to_registry
- _save_last_project
- _list_projects
- resolve_project_input
- resolve_last_project_config

All functions take explicit parameters instead of relying on closure variables.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any

import yaml

from clio.config import AppConfig, load_config
from clio.ui.services.file_service import _save_atomic

_REGISTRY_LOCK = threading.RLock()


def _resolve_project_output_path(project_dir: Path, value: str | Path | None) -> Path | None:
    if value is None or str(value).strip() == "":
        return None
    out_path = Path(value)
    if not out_path.is_absolute():
        out_path = (project_dir / out_path).resolve()
    return out_path


def _project_output_dir(project_dir: Path) -> Path:
    """Return the project's output directory.

    project.yaml is authoritative for configuration. project.json output_dir is
    kept as a legacy fallback for projects created before the config split.
    """
    proj_yaml = project_dir / "project.yaml"
    if proj_yaml.is_file():
        try:
            data = yaml.safe_load(proj_yaml.read_text(encoding="utf-8")) or {}
            out = data.get("paths", {}).get("output_dir")
            resolved = _resolve_project_output_path(project_dir, out)
            if resolved is not None:
                return resolved
        except (AttributeError, OSError, yaml.YAMLError):
            pass

    proj_file = project_dir / "project.json"
    if proj_file.is_file():
        try:
            data = json.loads(proj_file.read_text(encoding="utf-8"))
            out = data.get("output_dir") or "output"
        except (json.JSONDecodeError, OSError):
            out = "output"
    else:
        out = "output"
    return _resolve_project_output_path(project_dir, out) or (project_dir / "output").resolve()


def _artifact_subdir_names(project_dir: Path | None = None) -> dict[str, str]:
    """Read custom output subdir names from project.yaml (GAP-P2-01)."""
    names = {
        "compressed": "compressed",
        "texts": "texts",
        "scripts": "scripts",
        "plans": "plans",
    }
    if project_dir is None:
        return names
    proj_yaml = project_dir / "project.yaml"
    if not proj_yaml.is_file():
        return names
    try:
        data = yaml.safe_load(proj_yaml.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return names
    if not isinstance(data, dict):
        return names
    analyze = data.get("analyze") if isinstance(data.get("analyze"), dict) else {}
    script = data.get("script") if isinstance(data.get("script"), dict) else {}
    plan = data.get("plan") if isinstance(data.get("plan"), dict) else {}
    for key, section, field in (
        ("compressed", analyze, "compressed_subdir"),
        ("texts", analyze, "texts_subdir"),
        ("scripts", script, "scripts_subdir"),
        ("plans", plan, "plans_subdir"),
    ):
        raw = section.get(field) if isinstance(section, dict) else None
        if raw is None or str(raw).strip() == "":
            continue
        name = Path(str(raw)).name
        if name and name not in (".", ".."):
            names[key] = name
    return names


def _detect_steps(proj_output_dir: Path, *, project_dir: Path | None = None) -> dict[str, bool]:
    """Infer which pipeline steps are complete from the filesystem."""
    steps: dict[str, bool] = {}
    if not proj_output_dir.is_dir():
        return {k: False for k in ("compress", "analyze", "scripts", "plan", "label", "cut")}
    sub = _artifact_subdir_names(project_dir)
    comp = proj_output_dir / sub["compressed"]
    try:
        steps["compress"] = comp.is_dir() and any(comp.iterdir())
    except (PermissionError, OSError):
        steps["compress"] = False
    texts = _find_texts_dirs_for_steps(proj_output_dir, preferred_subdir=sub["texts"])
    try:
        steps["analyze"] = any(any(True for _ in t.iterdir()) for t in texts)
    except (PermissionError, OSError):
        steps["analyze"] = False
    scripts_dir = proj_output_dir / sub["scripts"]
    try:
        steps["scripts"] = scripts_dir.is_dir() and any(scripts_dir.iterdir())
    except (PermissionError, OSError):
        steps["scripts"] = False
    plans_dir = proj_output_dir / sub["plans"]
    try:
        steps["plan"] = plans_dir.is_dir() and any(plans_dir.iterdir())
    except (PermissionError, OSError):
        steps["plan"] = False
    try:
        steps["label"] = (proj_output_dir / "labeled").is_dir() and any((proj_output_dir / "labeled").iterdir())
    except (PermissionError, OSError):
        steps["label"] = False
    try:
        steps["cut"] = (proj_output_dir / "cuts").is_dir() and any((proj_output_dir / "cuts").iterdir())
    except (PermissionError, OSError):
        steps["cut"] = False
    return steps


def _find_texts_dirs_for_steps(output_dir: Path, *, preferred_subdir: str) -> list[Path]:
    from clio.ui.services.file_service import _find_texts_dirs

    return _find_texts_dirs(output_dir, preferred_subdir=preferred_subdir)


def _registry_path(config_path: Path | None) -> Path:
    if config_path:
        return config_path.parent / "projects.json"
    return Path("projects.json")


def _read_registry(registry_file: Path) -> dict[str, Any]:
    """Load projects.json or raise if the on-disk file is corrupt (GAP-P2-02).

    A missing file is treated as empty. A present but unparseable file is
    copied to a ``.corrupt.*`` sidecar and must not be rewritten from defaults.
    """
    if not registry_file.is_file():
        return {"projects": []}
    try:
        reg = json.loads(registry_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        quarantine = registry_file.with_name(registry_file.name + f".corrupt.{os.urandom(4).hex()}")
        try:
            shutil.copy2(registry_file, quarantine)
        except OSError:
            pass
        raise ValueError(f"projects.json 已损坏，已隔离为 {quarantine.name}；拒绝覆盖") from exc
    if not isinstance(reg, dict):
        raise ValueError("projects.json 结构无效；拒绝覆盖")
    return reg


def _registry_entry_path(entry: Any) -> str | None:
    """Normalize a projects.json entry to a directory path string."""
    if isinstance(entry, dict):
        raw = entry.get("project_dir") or entry.get("input_dir") or ""
    else:
        raw = str(entry) if entry else ""
    raw = str(raw).strip()
    return raw or None


def _registry_project_paths(reg: dict[str, Any]) -> list[str]:
    """Return project path strings from registry, skipping invalid entries."""
    out: list[str] = []
    for entry in reg.get("projects", []) or []:
        p = _registry_entry_path(entry)
        if p:
            out.append(p)
    return out


def _remove_from_registry(dir_path: str, config_path: Path | None) -> bool:
    """Remove a project from the registry. Returns True if an entry was removed."""
    with _REGISTRY_LOCK:
        registry_file = _registry_path(config_path)
        if not registry_file.is_file():
            return False
        try:
            reg = _read_registry(registry_file)
        except ValueError:
            return False
        try:
            normalized = str(Path(dir_path).resolve())
        except OSError:
            normalized = str(Path(dir_path))
        paths = _registry_project_paths(reg)
        kept: list[str] = []
        for p in paths:
            try:
                if str(Path(p).resolve()) == normalized:
                    continue
            except OSError:
                if p == dir_path:
                    continue
            kept.append(p)
        if kept == paths:
            return False
        data: dict[str, Any] = {"projects": kept}
        last_project = reg.get("last_project")
        if last_project:
            last_name = last_project.get("name") if isinstance(last_project, dict) else last_project
            if last_name in {Path(p).name for p in kept}:
                data["last_project"] = last_project
        _save_atomic(registry_file, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
        return True


def _add_to_registry(dir_path: str, config_path: Path | None) -> None:
    with _REGISTRY_LOCK:
        registry_file = _registry_path(config_path)
        reg = _read_registry(registry_file)
        paths = _registry_project_paths(reg)
        last_project = reg.get("last_project")
        try:
            normalized = str(Path(dir_path).resolve())
        except OSError:
            normalized = str(Path(dir_path))
        resolved_set = set()
        for p in paths:
            try:
                resolved_set.add(str(Path(p).resolve()))
            except OSError:
                resolved_set.add(p)
        if normalized not in resolved_set:
            paths.append(normalized)
        data: dict[str, Any] = {"projects": paths}
        if last_project:
            data["last_project"] = last_project
        _save_atomic(registry_file, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))


def _save_last_project(
    name: str, config_path: Path | None, input_dir: str | None = None, project_dir: str | None = None
) -> None:
    """Persist the currently active project for auto-load on next startup.

    Stores name + project_dir. `input_dir` remains a keyword alias for callers.
    """
    with _REGISTRY_LOCK:
        registry_file = _registry_path(config_path)
        reg = _read_registry(registry_file)
        paths = _registry_project_paths(reg)
        dir_value = project_dir or input_dir
        last_project: str | dict[str, str] = {"name": name, "project_dir": dir_value} if dir_value else name
        data: dict[str, Any] = {"projects": paths, "last_project": last_project}
        _save_atomic(registry_file, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))


def _list_projects(
    config_path: Path | None,
    project_dir: Path,
    current_project_name: str | None = None,
    current_project_dir: str | None = None,
) -> list[dict[str, Any]]:
    """List all available projects."""
    projects: list[dict[str, Any]] = []
    seen_dirs: set[str] = set()

    # 1. From the registry file (known projects)
    registry_file = _registry_path(config_path)
    registered_paths: list[str] = []
    if registry_file.is_file():
        try:
            reg = json.loads(registry_file.read_text(encoding="utf-8"))
            registered_paths = _registry_project_paths(reg)
        except (json.JSONDecodeError, OSError):
            registered_paths = []
    for p_str in registered_paths:
        p = Path(p_str)
        proj_file = p / "project.json"
        if not proj_file.is_file():
            continue
        try:
            data = json.loads(proj_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        name = data.get("name") or p.name
        version = data.get("version", 1)
        proj_out = _project_output_dir(p)
        seen_dirs.add(str(p.resolve()))
        has_videos_json = (p / "videos.json").is_file()
        yaml_has_input = _project_yaml_has_input_dir(p)
        # True legacy: still has paths.input_dir → needs `python main.py migrate`
        # Missing videos.json alone is needs_videos (openable, empty selection)
        projects.append(
            {
                "name": name,
                "project_dir": str(p),
                "output_dir": str(proj_out),
                "currentDay": data.get("currentDay", "day1"),
                "source": data.get("source", "compressed"),
                "steps": _detect_steps(proj_out, project_dir=p),
                "createdAt": data.get("createdAt"),
                "updatedAt": data.get("updatedAt"),
                "is_current": (
                    str(p.resolve()) == current_project_dir
                    if current_project_dir
                    else (
                        name == current_project_name if current_project_name else p.resolve() == project_dir.resolve()
                    )
                ),
                "legacy": yaml_has_input,
                "needs_videos": not has_videos_json,
                "version": version if has_videos_json and not yaml_has_input else version,
            }
        )

    # 2. Include current project_dir fallback only when an explicit project was requested
    if current_project_name:
        cur_resolved = str(project_dir.resolve())
        if cur_resolved not in seen_dirs:
            proj_file = project_dir / "project.json"
            if proj_file.is_file():
                try:
                    data = json.loads(proj_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    data = {}
            else:
                data = {}
            name = data.get("name") or project_dir.name
            proj_out = _project_output_dir(project_dir)
            has_videos_json = (project_dir / "videos.json").is_file()
            yaml_has_input = _project_yaml_has_input_dir(project_dir)
            projects.append(
                {
                    "name": name,
                    "project_dir": str(project_dir),
                    "output_dir": str(proj_out),
                    "currentDay": data.get("currentDay", "day1"),
                    "source": data.get("source", "compressed"),
                    "steps": _detect_steps(proj_out, project_dir=p),
                    "createdAt": data.get("createdAt"),
                    "updatedAt": data.get("updatedAt"),
                    "is_current": (
                        str(project_dir.resolve()) == current_project_dir
                        if current_project_dir
                        else (name == current_project_name if current_project_name else True)
                    ),
                    "legacy": yaml_has_input,
                    "needs_videos": not has_videos_json,
                    "version": data.get("version", 1),
                }
            )

    return projects


def _read_project_name(p: Path) -> str | None:
    """Read project name from project.json."""
    proj_file = p / "project.json"
    if not proj_file.is_file():
        return None
    try:
        data = json.loads(proj_file.read_text(encoding="utf-8"))
        return data.get("name")
    except (json.JSONDecodeError, OSError):
        return None


def _project_yaml_has_input_dir(project_dir: Path) -> bool:
    """True when project.yaml still declares the removed paths.input_dir field."""
    proj_yaml = project_dir / "project.yaml"
    if not proj_yaml.is_file():
        return False
    try:
        data = yaml.safe_load(proj_yaml.read_text(encoding="utf-8")) or {}
        paths = data.get("paths") or {}
        return bool(paths.get("input_dir"))
    except (OSError, yaml.YAMLError, AttributeError):
        return False


def collect_allowed_project_paths(default_input: Path, config_path: Path | None) -> set[str]:
    """Absolute path strings allowed as project roots (serve root + registry)."""
    allowed: set[str] = {str(Path(default_input).resolve())}
    if config_path is None or not isinstance(config_path, Path):
        return allowed
    registry_file = _registry_path(config_path)
    if not registry_file.is_file():
        return allowed
    try:
        text = registry_file.read_text(encoding="utf-8")
        reg = json.loads(text) if isinstance(text, str) else {}
    except (json.JSONDecodeError, OSError, TypeError):
        return allowed
    for p in _registry_project_paths(reg if isinstance(reg, dict) else {}):
        try:
            allowed.add(str(Path(p).resolve()))
        except OSError:
            allowed.add(p)
    return allowed


def is_under_root(path: Path, root: Path) -> bool:
    """True if *path* is *root* or a descendant (both resolved)."""
    try:
        path_r = path.expanduser().resolve()
        root_r = root.expanduser().resolve()
        path_r.relative_to(root_r)
        return True
    except (OSError, ValueError):
        return False


class ProjectResolutionError(ValueError):
    """Raised when an explicit project selector cannot be resolved.

    Carries the HTTP status a route should answer with: 400 (invalid input),
    404 (unknown project) or 409 (ambiguous match). Callers that only want the
    best-effort default fallback must not pass an explicit selector.
    """

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def resolve_project_input(qs: dict, input_dir: Path, config_path: Path | None) -> Path:
    """Resolve project directory from query params; default to current project_dir.

    Priority:
      1. project_dir / input_dir query param (direct path, unambiguous)
      2. project name query param (may be ambiguous)

    P1-28: when the caller passes an *explicit* selector (project_dir/input_dir
    or project name) that cannot be resolved, this raises ``ProjectResolutionError``
    instead of silently falling back to the default — an invalid dir, an unknown
    name or a duplicate name must surface as a clear 400/404/409, not run against
    the wrong project.
    """
    input_dir_raw = qs.get("project_dir", [None])[0] or qs.get("input_dir", [None])[0]
    if input_dir_raw:
        candidate = Path(input_dir_raw).resolve()
        allowed_paths = collect_allowed_project_paths(input_dir, config_path)
        if candidate.is_dir() and str(candidate) in allowed_paths:
            return candidate
        raise ProjectResolutionError(
            404 if candidate.is_dir() else 400,
            f"project directory not allowed or missing: {input_dir_raw}",
        )

    project_name = qs.get("project", [None])[0]
    if not project_name:
        return input_dir

    candidates: list[Path] = []
    seen: set[str] = set()

    def _score(p: Path) -> int:
        s = 0
        if p.name == project_name:
            s += 10
        if p.resolve() == input_dir.resolve():
            s += 5
        return s

    # 1. Registry first (user-added order)
    registry_file = _registry_path(config_path)
    if registry_file.is_file():
        try:
            reg = json.loads(registry_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            reg = {}
        for p_str in _registry_project_paths(reg):
            p = Path(p_str)
            resolved = str(p.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            name = _read_project_name(p)
            if name == project_name:
                candidates.append(p)

    # 2. Sibling directories (auto-discovery)
    projects_root = input_dir.parent
    if projects_root.is_dir():
        for p in sorted(projects_root.iterdir()):
            if not p.is_dir():
                continue
            resolved = str(p.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            name = _read_project_name(p)
            if name == project_name:
                candidates.append(p)

    if not candidates:
        raise ProjectResolutionError(404, f"unknown project name: {project_name}")
    if len(candidates) == 1:
        return candidates[0]
    candidates.sort(key=_score, reverse=True)
    raise ProjectResolutionError(409, f"ambiguous project name: {project_name}")


def resolve_last_project_config(config: AppConfig, config_path: Path | None) -> AppConfig:
    """If registry has a last_project, attempt to load its config instead of default.

    Supports both legacy (string name) and new (dict with name+input_dir) formats.
    """
    if not config_path:
        return config
    reg_file = _registry_path(config_path)
    if not reg_file.is_file():
        return config
    try:
        reg = json.loads(reg_file.read_text(encoding="utf-8"))
        last_project = reg.get("last_project")
        if not last_project:
            return config

        # New format: dict with project_dir / input_dir — resolve directly
        if isinstance(last_project, dict):
            input_dir_raw = last_project.get("project_dir") or last_project.get("input_dir")
            if input_dir_raw:
                p = Path(input_dir_raw)
                if p.is_dir():
                    return load_config(config_path, project_dir=p)

        # Legacy format: string name — match by project.json name
        last_name = last_project.get("name") if isinstance(last_project, dict) else last_project
        if not last_name:
            return config
        for p_str in reg.get("projects", []):
            p = Path(p_str)
            proj_file = p / "project.json"
            if not proj_file.is_file():
                continue
            data = json.loads(proj_file.read_text(encoding="utf-8"))
            if data.get("name") == last_name:
                return load_config(config_path, project_dir=p)
        return config
    except Exception:
        return config


def resolve_project_dir(qs: dict, project_dir: Path, config_path: Path | None) -> Path:
    """Alias for resolve_project_input (project_dir naming)."""
    return resolve_project_input(qs, project_dir, config_path)
