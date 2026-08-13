"""Route handlers: /api/projects, /api/project, /api/project/create, /api/project/add"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clio.ui.services.file_service import _create_project_yaml, _save_atomic
from clio.ui.services.project_service import (
    _add_to_registry,
    _detect_steps,
    _list_projects,
    _project_output_dir,
    _registry_path,
    _registry_project_paths,
    _remove_from_registry,
    _save_last_project,
)

if TYPE_CHECKING:
    from clio.ui.handler_protocol import HandlerProtocol


def handle_get_project(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """Handle GET /api/project. Read-only: does NOT modify the registry."""
    proj_dir = handler._resolve_project_dir(qs)
    proj_file = proj_dir / "project.json"
    data = {}
    if proj_file.is_file():
        try:
            data = json.loads(proj_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    merged = {**handler.DEFAULT_PROJECT, **data}
    proj_out = _project_output_dir(proj_dir)
    merged["steps"] = _detect_steps(proj_out)
    handler._send_json(merged)


def handle_post_project_select(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    """Handle POST /api/project/select. Records the project as last-used."""
    qs_project = obj.get("project") or qs.get("project", [None])[0]
    if not qs_project:
        return handler._send_json({"ok": False, "error": "missing project"}, 400)
    proj_dir = handler._resolve_project_dir(qs)
    config_path = handler.config_path
    try:
        _save_last_project(qs_project, config_path, project_dir=str(proj_dir))
    except ValueError as exc:
        return handler._send_json({"ok": False, "error": str(exc)}, 500)
    handler._send_json({"ok": True})


def handle_get_projects(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """Handle GET /api/projects."""
    req_project = qs.get("project", [None])[0]
    req_project_dir = qs.get("project_dir", [None])[0] or qs.get("input_dir", [None])[0]
    config_path = handler.config_path
    project_dir = handler.project_dir
    reg_file = _registry_path(config_path)
    last_project_payload: dict[str, str | None] | None = None
    if reg_file.is_file():
        try:
            reg = json.loads(reg_file.read_text(encoding="utf-8"))
            last_project = reg.get("last_project")
            if isinstance(last_project, dict):
                last_project_payload = {
                    "name": last_project.get("name"),
                    "project_dir": last_project.get("project_dir") or last_project.get("input_dir"),
                }
            elif last_project:
                last_project_payload = {"name": str(last_project), "project_dir": None}
        except (json.JSONDecodeError, OSError):
            pass
    projects = _list_projects(config_path, project_dir, req_project, req_project_dir)
    # Prune stale _config_cache entries for projects that no longer exist
    cache = handler.__class__._config_cache
    valid_dirs = {str(Path(p["project_dir"]).resolve()) for p in projects}
    for k in cache.keys():
        if k not in valid_dirs:
            cache.invalidate_key(k)
    # Resolve project_dir from the project list when registry only stored a name
    if last_project_payload and not last_project_payload.get("project_dir"):
        match = next((p for p in projects if p.get("name") == last_project_payload.get("name")), None)
        if match:
            last_project_payload["project_dir"] = match.get("project_dir")
    handler._send_json({"projects": projects, "last_project": last_project_payload})


def handle_put_project(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    """Handle PUT /api/project."""
    proj_dir = handler._resolve_project_dir(qs)
    proj_file = proj_dir / "project.json"
    data = {}
    if proj_file.is_file():
        try:
            data = json.loads(proj_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    merged = {**handler.DEFAULT_PROJECT, **data, **obj}
    merged["updatedAt"] = datetime.datetime.now().isoformat(timespec="seconds")
    if not proj_file.is_file():
        merged["createdAt"] = merged["updatedAt"]
    proj_dir.mkdir(parents=True, exist_ok=True)
    _save_atomic(proj_file, json.dumps(merged, ensure_ascii=False, indent=2).encode("utf-8"))
    config_path = handler.config_path
    try:
        _save_last_project(merged.get("name") or proj_dir.name, config_path, project_dir=str(proj_dir))
    except ValueError as exc:
        return handler._send_json({"ok": False, "error": str(exc)}, 500)
    handler._send_json({"ok": True})


def handle_post_project_create(handler: HandlerProtocol, obj: dict) -> None:
    """Handle POST /api/project/create."""
    config_path = handler.config_path

    name = (obj.get("name") or "").strip()
    project_dir_raw = (obj.get("project_dir") or obj.get("input_dir") or "").strip()
    output_dir_raw = (obj.get("output_dir") or "").strip()
    if not name:
        return handler._send_json({"ok": False, "error": "name is required"}, 400)
    if not project_dir_raw:
        return handler._send_json({"ok": False, "error": "project_dir is required"}, 400)
    input_path = Path(project_dir_raw)
    if not input_path.is_dir():
        return handler._send_json({"ok": False, "error": f"project_dir not found: {project_dir_raw}"}, 400)
    proj_file = input_path / "project.json"
    if proj_file.is_file():
        return handler._send_json({"ok": False, "error": f"目录已存在项目，请改用“打开项目”：{project_dir_raw}"}, 409)
    if output_dir_raw:
        proj_out = Path(output_dir_raw)
    else:
        proj_out = input_path / "output"
    now = datetime.datetime.now().isoformat(timespec="seconds")
    proj_data = {
        "name": name,
        "version": 2,
        "output_dir": str(proj_out),
        "currentDay": "day1",
        "source": "compressed",
        "lastEntity": None,
        "lastVideo": None,
        "createdAt": now,
        "updatedAt": now,
    }
    _save_atomic(proj_file, json.dumps(proj_data, ensure_ascii=False, indent=2).encode("utf-8"))
    # Auto-create project.yaml; a write against an existing global config must not
    # silently fall through to registry registration (P1-29).
    yaml_path = _create_project_yaml(input_path, config_path, proj_out)
    if config_path and config_path.is_file() and yaml_path is None:
        return handler._send_json({"ok": False, "error": f"创建 project.yaml 失败：{project_dir_raw}"}, 500)
    # New projects start with an empty selection (videos.json)
    from clio.tasks._video_loader import save_selected_videos

    if not (input_path / "videos.json").is_file():
        save_selected_videos(input_path, [])
    handler.__class__._config_cache.invalidate_key(str(input_path.resolve()))
    try:
        _add_to_registry(str(input_path), config_path)
    except ValueError as exc:
        return handler._send_json({"ok": False, "error": str(exc)}, 500)
    handler._send_json(
        {"ok": True, "project": {"name": name, "project_dir": str(input_path), "output_dir": str(proj_out)}}
    )


def handle_post_project_add(handler: HandlerProtocol, obj: dict) -> None:
    """Handle POST /api/project/add."""
    config_path = handler.config_path

    project_dir_raw = (obj.get("project_dir") or obj.get("input_dir") or "").strip()
    if not project_dir_raw:
        return handler._send_json({"ok": False, "error": "project_dir is required"}, 400)
    input_path = Path(project_dir_raw)
    if not input_path.is_dir():
        return handler._send_json({"ok": False, "error": f"目录不存在: {project_dir_raw}"}, 400)
    proj_file = input_path / "project.json"
    if not proj_file.is_file():
        # Auto-create project.json + project.yaml (similar to create project)
        proj_out = input_path / "output"
        now = datetime.datetime.now().isoformat(timespec="seconds")
        proj_data = {
            "name": input_path.name,
            "version": 2,
            "output_dir": str(proj_out),
            "currentDay": "day1",
            "source": "compressed",
            "lastEntity": None,
            "lastVideo": None,
            "createdAt": now,
            "updatedAt": now,
        }
        _save_atomic(proj_file, json.dumps(proj_data, ensure_ascii=False, indent=2).encode("utf-8"))
        # P1-29: don't proceed when a configured global exists but project.yaml can't be written.
        if _create_project_yaml(input_path, config_path, proj_out) is None and config_path and config_path.is_file():
            return handler._send_json({"ok": False, "error": f"创建 project.yaml 失败：{project_dir_raw}"}, 500)
        from clio.tasks._video_loader import save_selected_videos

        if not (input_path / "videos.json").is_file():
            save_selected_videos(input_path, [])
        handler.__class__._config_cache.invalidate_key(str(input_path.resolve()))
        name = input_path.name
    else:
        try:
            data = json.loads(proj_file.read_text(encoding="utf-8"))
            name = data.get("name") or input_path.name
        except (json.JSONDecodeError, OSError) as e:
            return handler._send_json({"ok": False, "error": f"无法读取 project.json: {e}"}, 400)
        # Opening an existing dir: ensure videos.json exists so project is non-legacy
        from clio.tasks._video_loader import save_selected_videos

        if not (input_path / "videos.json").is_file():
            save_selected_videos(input_path, [])
    try:
        _add_to_registry(str(input_path), config_path)
    except ValueError as exc:
        return handler._send_json({"ok": False, "error": str(exc)}, 500)
    handler._send_json({"ok": True, "project": {"name": name, "project_dir": str(input_path)}})


def handle_post_project_remove(handler: HandlerProtocol, obj: dict) -> None:
    """Handle POST /api/project/remove.

    Prefer stable ``project_dir`` / ``input_dir``. Name-only delete is allowed
    only when exactly one registry entry matches; ambiguous names return 409
    (GAP-P2-13).
    """
    config_path = handler.config_path
    project_name = (obj.get("name") or "").strip()
    input_dir_raw = (obj.get("project_dir") or obj.get("input_dir") or "").strip()
    if not project_name and not input_dir_raw:
        return handler._send_json({"ok": False, "error": "需要 project_dir 或唯一的 name"}, 400)

    targets: list[tuple[str, str]] = []  # (path, display_name)

    if input_dir_raw:
        try:
            normalized = str(Path(input_dir_raw).resolve())
        except OSError:
            normalized = str(Path(input_dir_raw))
        display = project_name or Path(normalized).name
        proj_file = Path(normalized) / "project.json"
        if proj_file.is_file():
            try:
                display = json.loads(proj_file.read_text(encoding="utf-8")).get("name") or display
            except (json.JSONDecodeError, OSError):
                pass
        targets.append((normalized, display))
    else:
        reg_file = _registry_path(config_path)
        matches: list[tuple[str, str]] = []
        if reg_file.is_file():
            try:
                reg = json.loads(reg_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                reg = {"projects": []}
            for p_str in _registry_project_paths(reg):
                p = Path(p_str)
                proj_file = p / "project.json"
                name = p.name
                if proj_file.is_file():
                    try:
                        name = json.loads(proj_file.read_text(encoding="utf-8")).get("name") or name
                    except (json.JSONDecodeError, OSError):
                        continue
                if name == project_name:
                    try:
                        matches.append((str(p.resolve()), name))
                    except OSError:
                        matches.append((p_str, name))
        if not matches:
            return handler._send_json(
                {"ok": False, "error": f"未找到名为 {project_name!r} 的项目", "removed_count": 0},
                404,
            )
        if len(matches) > 1:
            return handler._send_json(
                {
                    "ok": False,
                    "error": f"存在 {len(matches)} 个同名项目，请使用 project_dir 精确删除",
                    "matches": [{"name": n, "project_dir": d} for d, n in matches],
                    "count": len(matches),
                },
                409,
            )
        targets = matches

    removed: list[dict[str, str]] = []
    for path_str, name in targets:
        if _remove_from_registry(path_str, config_path):
            removed.append({"name": name, "project_dir": path_str})

    if not removed:
        return handler._send_json(
            {"ok": False, "error": "项目不在注册表中", "removed_count": 0},
            404,
        )
    handler._send_json({"ok": True, "removed": removed, "removed_count": len(removed)})


def handle_post_project_migrate(handler: HandlerProtocol, obj: dict) -> None:
    """Handle POST /api/project/migrate — migrate one legacy project to videos.json."""
    from clio.tasks.migrate import run_migrate

    config_path = handler.config_path
    if not config_path:
        return handler._send_json({"ok": False, "error": "config_path not available"}, 500)

    project_dir_raw = (obj.get("project_dir") or obj.get("input_dir") or "").strip()
    if not project_dir_raw:
        return handler._send_json({"ok": False, "error": "project_dir is required"}, 400)
    project_path = Path(project_dir_raw)
    if not project_path.is_dir():
        return handler._send_json({"ok": False, "error": f"project_dir not found: {project_dir_raw}"}, 400)
    if not (project_path / "project.yaml").is_file():
        return handler._send_json({"ok": False, "error": "project.yaml not found"}, 400)

    updated, errors = run_migrate(Path(config_path), from_path=project_path)
    if updated <= 0:
        if any("已是新结构" in e for e in errors):
            return handler._send_json(
                {
                    "ok": True,
                    "migrated": False,
                    "project_dir": str(project_path.resolve()),
                    "message": "项目已是新结构",
                }
            )
        msg = errors[0] if errors else "nothing to migrate"
        return handler._send_json({"ok": False, "error": msg, "errors": errors}, 400)

    handler.__class__._config_cache.invalidate_key(str(project_path.resolve()))
    handler._send_json(
        {
            "ok": True,
            "migrated": True,
            "project_dir": str(project_path.resolve()),
            "errors": errors,
        }
    )
