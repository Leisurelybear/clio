"""Migrate legacy projects (input_dir-based) to project_dir + videos.json."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml

from clio.tasks._video_loader import load_selected_videos, save_selected_videos
from clio.utils import find_videos


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
            for entry in reg.get("projects", []):
                if isinstance(entry, dict):
                    p_str = entry.get("project_dir") or entry.get("input_dir") or ""
                else:
                    p_str = str(entry)
                if not p_str:
                    continue
                p = Path(p_str)
                if p.is_dir() and (p / "project.yaml").is_file():
                    projects_to_migrate.append(p.resolve())
        except Exception as e:
            errors.append(f"读取注册表失败: {e}")

    # 2. From --from flag
    if from_path:
        if from_path.is_dir() and (from_path / "project.yaml").is_file():
            projects_to_migrate.append(from_path.resolve())
        else:
            errors.append(f"--from 路径无效或不含 project.yaml: {from_path}")

    # Dedup while preserving order
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in projects_to_migrate:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    projects_to_migrate = unique

    if not projects_to_migrate:
        return 0, errors or ["未发现可迁移项目"]

    # Backup registry once
    if registry_file.is_file():
        bak = registry_file.with_suffix(".json.migrate-bak")
        try:
            shutil.copy2(registry_file, bak)
        except OSError as e:
            errors.append(f"备份 projects.json 失败: {e}")

    updated = 0
    for old_dir in projects_to_migrate:
        try:
            ok, msg = _migrate_one(old_dir, config_path, registry_file)
            if ok:
                updated += 1
                print(msg)
            else:
                errors.append(msg)
        except Exception as e:
            errors.append(f"{old_dir}: 迁移异常: {e}")

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
        except (json.JSONDecodeError, OSError):
            pass
    return (old_dir / "output").resolve()


def _migrate_one(old_dir: Path, config_path: Path, registry_file: Path) -> tuple[bool, str]:
    """Migrate a single project directory. Returns (ok, message)."""
    old_yaml = old_dir / "project.yaml"
    if not old_yaml.is_file():
        return False, f"{old_dir}: 无 project.yaml"

    videos_json = old_dir / "videos.json"
    try:
        with old_yaml.open(encoding="utf-8") as f:
            old_raw: dict[str, Any] = yaml.safe_load(f) or {}
    except Exception as e:
        return False, f"{old_dir}: 读取 project.yaml 失败: {e}"

    paths_raw = dict(old_raw.get("paths") or {})
    has_input = bool(paths_raw.get("input_dir"))
    if videos_json.is_file() and not has_input:
        return False, f"{old_dir}: 已是新结构，跳过"

    # Resolve old input_dir for video discovery
    old_input_raw = paths_raw.get("input_dir", ".")
    if old_input_raw in (None, "", "."):
        old_input_path = old_dir
    else:
        candidate = Path(str(old_input_raw))
        old_input_path = candidate if candidate.is_absolute() else (old_dir / candidate).resolve()

    name = old_raw.get("name") or old_dir.name
    old_json = old_dir / "project.json"
    abs_output = _resolve_output_dir(old_dir, paths_raw, old_json)

    # Prefer in-place: keep project at old_dir, only write videos.json + strip yaml.
    # Non-in-place only when videos live outside old_dir AND we want a clean config home.
    # Even then we preserve absolute output_dir so existing artifacts stay reachable.
    if old_input_path.resolve() == old_dir.resolve() or videos_json.is_file() or abs_output.exists():
        # Stay in place whenever output already has work, or videos are collocated
        new_dir = old_dir
        in_place = True
    else:
        new_dir = (config_path.parent / "projects" / name).resolve()
        in_place = False

    print(f"迁移: {old_dir} → {new_dir}" + (" (原地)" if in_place else ""))
    new_dir.mkdir(parents=True, exist_ok=True)

    # 1. Backup old yaml
    bak_yaml = old_dir / "project.yaml.migrate-bak"
    if not bak_yaml.is_file():
        try:
            shutil.copy2(old_yaml, bak_yaml)
        except OSError as e:
            return False, f"{old_dir}: 备份 project.yaml 失败: {e}"

    # 2. Write new project.yaml without input_dir/recursive (atomic via tmp+rename)
    new_raw = dict(old_raw)
    new_paths = dict(paths_raw)
    new_paths.pop("input_dir", None)
    new_paths.pop("recursive", None)
    # Always store absolute output_dir so non-in-place moves keep artifacts
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
    except Exception as e:
        tmp_yaml.unlink(missing_ok=True)
        return False, f"{old_dir}: 写入 project.yaml 失败: {e}"

    # 3. project.json (atomic via tmp+rename)
    new_json = new_dir / "project.json"
    data: dict[str, Any] = {}
    if old_json.is_file():
        try:
            data = json.loads(old_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
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
        return False, f"{old_dir}: 写入 project.json 失败: {e}"

    # Verify writes succeeded before proceeding
    if not new_yaml.is_file() or not new_json.is_file():
        return False, f"{old_dir}: 写入验证失败（文件未生成）"
    try:
        verify_raw = yaml.safe_load(new_yaml.read_text(encoding="utf-8"))
        if not verify_raw or "paths" not in verify_raw:
            return False, f"{old_dir}: project.yaml 内容验证失败"
        verify_data = json.loads(new_json.read_text(encoding="utf-8"))
        if verify_data.get("version") != 2:
            return False, f"{old_dir}: project.json 版本验证失败"
    except Exception as e:
        return False, f"{old_dir}: 写入验证失败: {e}"

    # 4. videos.json — preserve curated list if the file already exists
    # (including intentional empty []). Only scan when the file is absent.
    videos_json_target = new_dir / "videos.json"
    videos_json_old = old_dir / "videos.json"
    if videos_json_target.is_file() or (old_dir.resolve() != new_dir.resolve() and videos_json_old.is_file()):
        existing = load_selected_videos(new_dir)
        if not existing and old_dir.resolve() != new_dir.resolve() and videos_json_old.is_file():
            existing = load_selected_videos(old_dir)
            save_selected_videos(new_dir, existing)
        videos = existing
        print(f"  保留已有 videos.json ({len(videos)} 个视频)")
    else:
        if old_input_path.is_dir():
            videos = find_videos(old_input_path, recursive=True)
            # Drop anything under the project's output tree (compressed artifacts)
            out_resolved = abs_output.resolve()
            videos = [v for v in videos if out_resolved not in v.resolve().parents and v.resolve() != out_resolved]
        else:
            videos = []
        save_selected_videos(new_dir, videos)
        print(f"  发现 {len(videos)} 个视频")

    # 5. Update registry
    _update_registry(registry_file, old_dir, new_dir, name)

    # 6. Non-in-place: retire old project.yaml (keep bak)
    if not in_place and old_yaml.is_file() and old_yaml.resolve() != (new_dir / "project.yaml").resolve():
        try:
            if not bak_yaml.is_file():
                old_yaml.rename(bak_yaml)
            else:
                old_yaml.unlink(missing_ok=True)
        except OSError:
            pass

    return True, f"已迁移 {name}: {new_dir} ({len(videos)} 视频, output={abs_output})"


def _update_registry(registry_file: Path, old_dir: Path, new_dir: Path, name: str) -> None:
    reg: dict[str, Any] = {"projects": []}
    if registry_file.is_file():
        try:
            reg = json.loads(registry_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            reg = {"projects": []}

    old_s = str(old_dir.resolve())
    new_s = str(new_dir.resolve())

    def _resolve_entry(entry: Any) -> str:
        if isinstance(entry, dict):
            p = entry.get("project_dir") or entry.get("input_dir") or ""
        else:
            p = str(entry) if entry else ""
        if not p:
            return ""
        try:
            return str(Path(p).resolve())
        except Exception:
            return p

    projects: list[str] = []
    for entry in reg.get("projects", []):
        resolved = _resolve_entry(entry)
        if not resolved or resolved in (old_s, new_s):
            continue
        if isinstance(entry, dict):
            projects.append(entry.get("project_dir") or entry.get("input_dir") or resolved)
        else:
            projects.append(str(entry))

    if new_s not in {_resolve_entry(p) for p in projects}:
        projects.append(new_s)
    projects = [p for p in projects if p]

    last = reg.get("last_project")
    should_update_last = False
    if isinstance(last, dict):
        last_dir = last.get("project_dir") or last.get("input_dir")
        if last_dir:
            try:
                if str(Path(last_dir).resolve()) == old_s:
                    should_update_last = True
            except Exception:
                pass
        if last.get("name") == name:
            should_update_last = True
    elif isinstance(last, str):
        if last == name:
            should_update_last = True
        else:
            try:
                if str(Path(last).resolve()) == old_s:
                    should_update_last = True
            except Exception:
                pass

    if should_update_last or last is None:
        last = {"name": name, "project_dir": new_s}

    out: dict[str, Any] = {"projects": projects, "last_project": last}
    registry_file.parent.mkdir(parents=True, exist_ok=True)
    registry_file.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
