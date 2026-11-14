"""Migrate legacy projects (input_dir-based) to project_dir + videos.json."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml

from clio.i18n import SUPPORTED_LOCALES, t
from clio.tasks._video_loader import load_selected_videos, save_selected_videos
from clio.utils import find_videos

_REGISTRY_READ_ERRORS = (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError)
_YAML_READ_ERRORS = (OSError, UnicodeDecodeError, yaml.YAMLError, TypeError, ValueError)
_VERIFY_ERRORS = (OSError, UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError, TypeError, ValueError)
_PATH_RESOLVE_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


def is_already_new_structure(message: str) -> bool:
    """True when a migrate error means the project already uses the new layout.

    The check is locale-independent: the marker is looked up in every supported
    catalog, so the caller never has to hard-code Chinese copy.
    """
    return any(t("task.migrate.already_new_suffix", locale=locale) in message for locale in SUPPORTED_LOCALES)


def run_migrate(config_path: Path, from_path: Path | None = None) -> tuple[int, list[str]]:
    """Scan and migrate old projects to the new project_dir + videos.json layout.

    Returns (updated_count, error_messages).
    """
    registry_file = config_path.parent / "projects.json"
    projects_to_migrate: list[Path] = []
    errors: list[str] = []

    # 1. From registry
    if registry_file.is_file():
        try:
            reg = json.loads(registry_file.read_text(encoding="utf-8"))
            if not isinstance(reg, dict):
                raise ValueError("projects.json root must be an object")
            for entry in reg.get("projects", []):
                if isinstance(entry, dict):
                    p_str = entry.get("project_dir") or entry.get("input_dir") or ""
                else:
                    p_str = str(entry)
                if not p_str:
                    continue
                p = Path(str(p_str))
                if p.is_dir() and (p / "project.yaml").is_file():
                    projects_to_migrate.append(p.resolve())
        except _REGISTRY_READ_ERRORS as e:
            errors.append(t("task.migrate.registry_read_failed", {"detail": e}))

    # 2. From --from flag
    if from_path:
        if from_path.is_dir() and (from_path / "project.yaml").is_file():
            projects_to_migrate.append(from_path.resolve())
        else:
            errors.append(t("task.migrate.invalid_from_path", {"path": from_path}))

    # Dedup while preserving order
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in projects_to_migrate:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    projects_to_migrate = unique

    if not projects_to_migrate:
        return 0, errors or [t("task.migrate.nothing_to_migrate")]

    # Backup registry once
    if registry_file.is_file():
        bak = registry_file.with_suffix(".json.migrate-bak")
        try:
            shutil.copy2(registry_file, bak)
        except OSError as e:
            errors.append(t("task.migrate.backup_projects_failed", {"detail": e}))

    updated = 0
    for old_dir in projects_to_migrate:
        try:
            ok, msg = _migrate_one(old_dir, config_path, registry_file)
            if ok:
                updated += 1
                print(msg)
            else:
                errors.append(msg)
        except Exception as e:  # noqa: BLE001 - one bad project must not stop the batch
            errors.append(t("task.migrate.migrate_error", {"dir": old_dir, "detail": e}))

    return updated, errors


def _resolve_output_dir(old_dir: Path, paths_raw: dict[str, Any], project_json: Path) -> Path:
    """Resolve the absolute output directory for a legacy project."""
    out_raw = paths_raw.get("output_dir")
    if out_raw:
        out_path = Path(str(out_raw))
        if not out_path.is_absolute():
            out_path = (old_dir / out_path).resolve()
        return out_path
    if project_json.is_file():
        try:
            data = json.loads(project_json.read_text(encoding="utf-8"))
            out_raw = data.get("output_dir")
            if out_raw:
                out_path = Path(str(out_raw))
                if not out_path.is_absolute():
                    out_path = (old_dir / out_path).resolve()
                return out_path
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass
    return (old_dir / "output").resolve()


def _read_legacy_project(old_dir: Path, old_yaml: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    with old_yaml.open(encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise ValueError("project.yaml root must be an object")
    paths_value = loaded.get("paths") or {}
    if not isinstance(paths_value, dict):
        raise ValueError("project.yaml paths must be an object")
    return loaded, dict(paths_value)


def _resolve_legacy_input_path(old_dir: Path, paths_raw: dict[str, Any]) -> Path:
    old_input_raw = paths_raw.get("input_dir", ".")
    if old_input_raw in (None, "", "."):
        return old_dir
    candidate = Path(str(old_input_raw))
    return candidate if candidate.is_absolute() else (old_dir / candidate).resolve()


def _select_migration_target(
    old_dir: Path,
    videos_json: Path,
    abs_output: Path,
    config_path: Path,
    name: str,
    old_input_path: Path,
) -> tuple[Path, bool]:
    if old_input_path.resolve() == old_dir.resolve() or videos_json.is_file() or abs_output.exists():
        return old_dir, True
    return (config_path.parent / "projects" / name).resolve(), False


def _write_migrated_yaml(
    old_dir: Path,
    old_raw: dict[str, Any],
    paths_raw: dict[str, Any],
    abs_output: Path,
    new_dir: Path,
) -> str | None:
    new_raw = dict(old_raw)
    new_paths = dict(paths_raw)
    new_paths.pop("input_dir", None)
    new_paths.pop("recursive", None)
    new_paths["output_dir"] = str(abs_output)
    new_raw["paths"] = new_paths
    new_yaml = new_dir / "project.yaml"
    tmp_yaml = new_yaml.with_suffix(".yaml.migrate-tmp")
    try:
        tmp_yaml.write_text(
            yaml.dump(new_raw, default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        tmp_yaml.replace(new_yaml)
    except (OSError, yaml.YAMLError) as e:
        tmp_yaml.unlink(missing_ok=True)
        return t("task.migrate.write_yaml_failed", {"dir": old_dir, "detail": e})
    return None


def _write_migrated_json(old_dir: Path, old_json: Path, new_dir: Path, name: str, abs_output: Path) -> str | None:
    new_json = new_dir / "project.json"
    data: dict[str, Any] = {}
    if old_json.is_file():
        try:
            loaded_data = json.loads(old_json.read_text(encoding="utf-8"))
            data = loaded_data if isinstance(loaded_data, dict) else {}
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            data = {}
    data["version"] = 2
    data.setdefault("name", name)
    data["output_dir"] = str(abs_output)
    data.setdefault("currentDay", "day1")
    data.setdefault("source", "compressed")
    tmp_json = new_json.with_suffix(".json.migrate-tmp")
    try:
        tmp_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_json.replace(new_json)
    except OSError as e:
        tmp_json.unlink(missing_ok=True)
        return t("task.migrate.write_json_failed", {"dir": old_dir, "detail": e})
    return None


def _verify_migrated_files(old_dir: Path, new_yaml: Path, new_json: Path) -> str | None:
    if not new_yaml.is_file() or not new_json.is_file():
        return t("task.migrate.write_verify_failed", {"dir": old_dir})
    try:
        verify_raw = yaml.safe_load(new_yaml.read_text(encoding="utf-8"))
        if not isinstance(verify_raw, dict) or "paths" not in verify_raw:
            return t("task.migrate.yaml_verify_failed", {"dir": old_dir})
        verify_data = json.loads(new_json.read_text(encoding="utf-8"))
        if not isinstance(verify_data, dict) or verify_data.get("version") != 2:
            return t("task.migrate.json_version_verify_failed", {"dir": old_dir})
    except _VERIFY_ERRORS as e:
        return t("task.migrate.write_verify_error", {"dir": old_dir, "detail": e})
    return None


def _load_or_scan_videos(
    old_dir: Path,
    new_dir: Path,
    old_input_path: Path,
    abs_output: Path,
    videos_json: Path,
) -> list[Path]:
    videos_json_target = new_dir / "videos.json"
    same_dir = old_dir.resolve() == new_dir.resolve()
    if videos_json_target.is_file() or (not same_dir and videos_json.is_file()):
        existing = load_selected_videos(new_dir)
        if not existing and not same_dir and videos_json.is_file():
            existing = load_selected_videos(old_dir)
            save_selected_videos(new_dir, existing)
        videos = existing
        print(t("task.migrate.keep_videos_json", {"count": len(videos)}))
        return videos
    if old_input_path.is_dir():
        videos = find_videos(old_input_path, recursive=True)
        out_resolved = abs_output.resolve()
        videos = [v for v in videos if out_resolved not in v.resolve().parents and v.resolve() != out_resolved]
    else:
        videos = []
    save_selected_videos(new_dir, videos)
    print(t("task.migrate.found_videos", {"count": len(videos)}))
    return videos


def _retire_legacy_yaml(old_dir: Path, new_dir: Path, old_yaml: Path, bak_yaml: Path, in_place: bool) -> None:
    if in_place or not old_yaml.is_file() or old_yaml.resolve() == (new_dir / "project.yaml").resolve():
        return
    try:
        if not bak_yaml.is_file():
            old_yaml.rename(bak_yaml)
        else:
            old_yaml.unlink(missing_ok=True)
    except OSError:
        pass


def _migrate_one(old_dir: Path, config_path: Path, registry_file: Path) -> tuple[bool, str]:
    """Migrate a single project directory. Returns (ok, message)."""
    old_yaml = old_dir / "project.yaml"
    if not old_yaml.is_file():
        return False, t("task.migrate.no_project_yaml", {"dir": old_dir})

    videos_json = old_dir / "videos.json"
    try:
        old_raw, paths_raw = _read_legacy_project(old_dir, old_yaml)
    except _YAML_READ_ERRORS as e:
        return False, t("task.migrate.read_yaml_failed", {"dir": old_dir, "detail": e})
    has_input = bool(paths_raw.get("input_dir"))
    if videos_json.is_file() and not has_input:
        return False, f"{old_dir}: " + t("task.migrate.already_new_suffix")

    old_input_path = _resolve_legacy_input_path(old_dir, paths_raw)
    name = old_raw.get("name") or old_dir.name
    old_json = old_dir / "project.json"
    abs_output = _resolve_output_dir(old_dir, paths_raw, old_json)
    new_dir, in_place = _select_migration_target(old_dir, videos_json, abs_output, config_path, name, old_input_path)

    print(
        t("task.migrate.migrating", {"src": old_dir, "dst": new_dir})
        + (t("task.migrate.in_place_suffix") if in_place else "")
    )
    new_dir.mkdir(parents=True, exist_ok=True)

    bak_yaml = old_dir / "project.yaml.migrate-bak"
    if not bak_yaml.is_file():
        try:
            shutil.copy2(old_yaml, bak_yaml)
        except OSError as e:
            return False, t("task.migrate.backup_yaml_failed", {"dir": old_dir, "detail": e})

    error = _write_migrated_yaml(old_dir, old_raw, paths_raw, abs_output, new_dir)
    if error:
        return False, error
    error = _write_migrated_json(old_dir, old_json, new_dir, name, abs_output)
    if error:
        return False, error
    new_yaml = new_dir / "project.yaml"
    new_json = new_dir / "project.json"
    error = _verify_migrated_files(old_dir, new_yaml, new_json)
    if error:
        return False, error

    videos = _load_or_scan_videos(old_dir, new_dir, old_input_path, abs_output, videos_json)
    _update_registry(registry_file, old_dir, new_dir, name)
    _retire_legacy_yaml(old_dir, new_dir, old_yaml, bak_yaml, in_place)

    return True, t(
        "task.migrate.migrated",
        {"name": name, "dir": new_dir, "count": len(videos), "output": abs_output},
    )


def _load_registry(registry_file: Path) -> dict[str, Any]:
    if not registry_file.is_file():
        return {"projects": []}
    try:
        loaded = json.loads(registry_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {"projects": []}
    return loaded if isinstance(loaded, dict) else {"projects": []}


def _resolve_registry_entry(entry: Any) -> str:
    if isinstance(entry, dict):
        path = entry.get("project_dir") or entry.get("input_dir") or ""
    else:
        path = str(entry) if entry else ""
    if not path:
        return ""
    try:
        return str(Path(path).resolve())
    except _PATH_RESOLVE_ERRORS:
        return path


def _registry_entry_value(entry: Any, resolved: str) -> str:
    if isinstance(entry, dict):
        return entry.get("project_dir") or entry.get("input_dir") or resolved
    return str(entry)


def _should_update_last_project(last: Any, name: str, old_s: str) -> bool:
    if isinstance(last, dict):
        last_dir = last.get("project_dir") or last.get("input_dir")
        if last_dir:
            try:
                if str(Path(last_dir).resolve()) == old_s:
                    return True
            except _PATH_RESOLVE_ERRORS:
                pass
        return last.get("name") == name
    if isinstance(last, str):
        if last == name:
            return True
        try:
            return str(Path(last).resolve()) == old_s
        except _PATH_RESOLVE_ERRORS:
            return False
    return False


def _update_registry(registry_file: Path, old_dir: Path, new_dir: Path, name: str) -> None:
    reg = _load_registry(registry_file)
    old_s = str(old_dir.resolve())
    new_s = str(new_dir.resolve())

    projects: list[str] = []
    for entry in reg.get("projects", []):
        resolved = _resolve_registry_entry(entry)
        if not resolved or resolved in (old_s, new_s):
            continue
        projects.append(_registry_entry_value(entry, resolved))

    if new_s not in {_resolve_registry_entry(p) for p in projects}:
        projects.append(new_s)
    projects = [p for p in projects if p]

    last = reg.get("last_project")
    if _should_update_last_project(last, name, old_s) or last is None:
        last = {"name": name, "project_dir": new_s}

    out: dict[str, Any] = {"projects": projects, "last_project": last}
    registry_file.parent.mkdir(parents=True, exist_ok=True)
    registry_file.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
