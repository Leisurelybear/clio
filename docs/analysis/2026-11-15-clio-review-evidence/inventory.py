"""Generate a tracked-file inventory and local review measurements.

The inventory is a coverage index, not a claim that every file was read line by line.
Only tracked source and already-produced coverage summaries are inspected.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
FOCUSED = set(
    [
        "main.py",
        "clio/main.py",
        "clio/pipeline.py",
        "clio/analyze.py",
        "clio/prompts.py",
        "clio/compress.py",
        "clio/cut.py",
        "clio/ffmpeg_install.py",
        "clio/vmeta.py",
        "clio/identity.py",
        "clio/index.py",
        "clio/utils.py",
        "clio/scene.py",
        "clio/gpmf.py",
        "clio/cut_snap.py",
        "clio/plan_input.py",
        "clio/plan_model.py",
        "clio/plan_readiness.py",
        "clio/plan_preview.py",
        "clio/progress.py",
        "clio/shutdown.py",
        "clio/processing_state.py",
        "clio/privacy.py",
        "clio/log.py",
        "clio/session_log.py",
        "clio/schema.py",
        "clio/config/models.py",
        "clio/config/loader.py",
        "clio/config/validators.py",
        "clio/ai/factory.py",
        "clio/ai/base.py",
        "clio/ai/gemini.py",
        "clio/ai/openai_compat.py",
        "clio/ai/structured.py",
        "clio/ai/provider_security.py",
        "clio/ai/token_usage.py",
        "clio/tasks/_helpers.py",
        "clio/tasks/_video_loader.py",
        "clio/tasks/compress.py",
        "clio/tasks/analyze.py",
        "clio/tasks/transcribe.py",
        "clio/tasks/consistency.py",
        "clio/tasks/plan.py",
        "clio/tasks/scripts.py",
        "clio/tasks/refine.py",
        "clio/tasks/cut.py",
        "clio/tasks/reindex.py",
        "clio/tasks/verify.py",
        "clio/tasks/label.py",
        "clio/tasks/cover.py",
        "clio/export/__init__.py",
        "clio/export/jianying.py",
        "clio/task_center/executor.py",
        "clio/task_center/manager.py",
        "clio/task_center/store.py",
        "clio/task_center/models.py",
        "clio/task_center/state_machine.py",
        "clio/ui/server.py",
        "clio/ui/handler_factory.py",
        "clio/ui/handler_security.py",
        "clio/ui/request_body.py",
        "clio/ui/route_table.py",
        "clio/ui/handler_protocol.py",
        "clio/ui/services/file_service.py",
        "clio/ui/services/project_service.py",
        "clio/ui/services/config_cache.py",
        "clio/ui/services/run_preview.py",
        "clio/ui/routes/refine.py",
        "clio/ui/routes/texts.py",
        "clio/ui/routes/plan.py",
        "clio/ui/routes/export.py",
        "clio/ui/routes/videos.py",
        "clio/ui/routes/waveform.py",
        "clio/ui/routes/run.py",
        "clio/ui/routes/config_routes.py",
        "clio/ui/routes/env_routes.py",
        "clio/desktop/app.py",
        "clio/desktop/api.py",
        "clio/ui/static/src/api.js",
        "clio/ui/static/src/state.js",
        "clio/ui/static/src/sidebar.js",
        "clio/ui/static/src/sidebar-data.js",
        "clio/ui/static/src/editor-refine.js",
        "clio/ui/static/src/editor-plan-persistence.js",
        "clio/ui/static/src/editor-plan.js",
        "clio/ui/static/src/editor-texts.js",
        "clio/ui/static/src/editor-voiceover.js",
        "clio/ui/static/src/runner.js",
        "clio/ui/static/src/runner-preview.js",
        "clio/ui/static/src/runner-stage-model.js",
        "clio/ui/static/src/viewer.js",
        "clio/ui/static/src/plan-preview-media.js",
        "clio/ui/static/src/plan-waveform.js",
        "clio/ui/static/src/video-url.js",
        "clio/ui/static/src/utils.js",
        "clio/ui/static/src/main.js",
        "clio/ui/static/src/waveform.js",
        "AGENTS.md",
        "ROADMAP.md",
        "docs/CONVENTIONS.md",
        "config.example.yaml",
        "docs/project.example.yaml",
        "templates/trip_context.md",
        "pyproject.toml",
        "package.json",
        "tsconfig.json",
        "vitest.config.js",
        ".github/workflows/test.yml",
        ".github/workflows/release.yml",
        "packaging/clio.spec",
        "docs/superpowers/agents/gotchas.md",
        "docs/superpowers/agents/maintenance-instructions.md",
        "docs/analysis/2026-11-13-clio-open-items-status.md",
    ]
)


def area(path: str) -> str:
    prefixes = (
        ("clio/tests/", "Python tests"),
        ("clio/ui/static/src/__tests__/", "Frontend tests"),
        ("clio/ui/static/", "Web frontend/assets"),
        ("clio/ui/routes/", "Web routes"),
        ("clio/ui/services/", "Web services"),
        ("clio/ui/", "HTTP infrastructure"),
        ("clio/task_center/", "Task runtime"),
        ("clio/config/", "Configuration"),
        ("clio/ai/", "AI providers"),
        ("clio/asr/", "ASR"),
        ("clio/tasks/", "Pipeline task adapters"),
        ("clio/export/", "Export"),
        ("clio/desktop/", "Desktop"),
        ("clio/", "Domain/media/CLI"),
        (".github/", "CI/release"),
        ("packaging/", "Packaging"),
        ("docs/", "Documentation"),
        ("templates/", "Templates"),
    )
    return next((name for prefix, name in prefixes if path.startswith(prefix)), "Root/tooling")


def main() -> None:
    tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    rows = []
    for name in tracked:
        raw = (ROOT / name).read_bytes()
        try:
            text = raw.decode("utf-8-sig")
            lines = len(text.splitlines())
        except UnicodeDecodeError:
            text, lines = "", ""
        tests = "/tests/" in name or "/__tests__/" in name
        depth = (
            "selected code sections / boundary review"
            if name in FOCUSED
            else (
                "suite executed; individual source not fully audited"
                if tests
                else "area-level inventory; no individual line-by-line claim"
            )
        )
        functions = classes = ""
        if name.endswith(".py"):
            tree = ast.parse(text, filename=name)
            functions = sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree))
            classes = sum(isinstance(node, ast.ClassDef) for node in ast.walk(tree))
        rows.append(
            {
                "path": name,
                "area": area(name),
                "lines": lines,
                "functions": functions,
                "classes": classes,
                "review_depth": depth,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    with (OUT / "file-inventory.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    python = [
        r
        for r in rows
        if r["path"].endswith(".py")
        and (r["path"].startswith("clio/") or r["path"] == "main.py")
        and "/tests/" not in r["path"]
    ]
    javascript = [
        r
        for r in rows
        if r["path"].startswith("clio/ui/static/src/") and r["path"].endswith(".js") and "/__tests__/" not in r["path"]
    ]
    metrics = {
        "baseline": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "tracked_files_at_inventory": len(rows),
        "files_by_area": dict(Counter(r["area"] for r in rows)),
        "selected_section_files": sum(r["path"] in FOCUSED for r in rows),
        "production_python_files": len(python),
        "production_python_lines": sum(r["lines"] for r in python),
        "production_javascript_files": len(javascript),
        "production_javascript_lines": sum(r["lines"] for r in javascript),
        "largest_source_files": [
            {"path": r["path"], "lines": r["lines"]}
            for r in sorted(python + javascript, key=lambda r: r["lines"], reverse=True)[:15]
        ],
        "python_coverage": json.loads(
            (ROOT / ".pytest_cache/review-2026-11-15-python-coverage.json").read_text(encoding="utf-8")
        )["totals"],
        "frontend_coverage": json.loads((ROOT / "coverage/coverage-summary.json").read_text(encoding="utf-8"))["total"],
        "note": "Line counts include blanks/comments. Coverage is execution evidence, not individual manual review. Inventory excludes untracked review deliverables and unrelated local files.",
    }
    (OUT / "measurements.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                key: value
                for key, value in metrics.items()
                if key not in {"python_coverage", "frontend_coverage", "largest_source_files", "files_by_area"}
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
