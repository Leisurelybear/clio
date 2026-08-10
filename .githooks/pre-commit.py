#!/usr/bin/env python3
"""Pre-commit hook: auto-format staged .py files with ruff."""

import subprocess
import sys
from pathlib import Path


def _reconfigure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_reconfigure_stdio()

REPO_ROOT = Path(__file__).resolve().parent.parent

# Locate ruff via project venv
ruff_candidates = [
    REPO_ROOT / ".venv" / "Scripts" / "ruff.exe",  # Windows
    REPO_ROOT / ".venv" / "bin" / "ruff",  # Unix
]
RUFF = next((p for p in ruff_candidates if p.is_file()), None)
if RUFF is None:
    # Fallback: try `python -m ruff`
    python = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        python = REPO_ROOT / ".venv" / "bin" / "python"
    if python.is_file():
        result = subprocess.run([str(python), "-m", "ruff", "--version"], capture_output=True, text=True, cwd=REPO_ROOT)
        if result.returncode == 0:
            # We'll use python -m ruff as the command
            RUFF = [str(python), "-m", "ruff"]
        else:
            print("[pre-commit] ruff not found, skipping format check")
            sys.exit(0)
    else:
        print("[pre-commit] no Python venv found, skipping format check")
        sys.exit(0)

if not isinstance(RUFF, list):
    RUFF = [str(RUFF)]

# Get staged .py files (excluding deletions)
result = subprocess.run(
    ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM", "--", "*.py"],
    capture_output=True,
    text=True,
    cwd=REPO_ROOT,
)
if result.returncode != 0 or not result.stdout.strip():
    sys.exit(0)

staged = [f for f in result.stdout.splitlines() if f]
if not staged:
    sys.exit(0)


def _has_unstaged_changes(path: str) -> bool:
    """True when the file has edits in the working tree that are NOT staged."""
    return subprocess.run(["git", "diff", "--quiet", "--", path], cwd=REPO_ROOT).returncode == 1


# P1-26: never rewrite the full working-tree file while only part of it is
# staged. Running `ruff format <file>` then `git add <file>` would swallow any
# unstaged hunks into the index, breaking partial staging.
partial = [_f for _f in staged if _has_unstaged_changes(_f)]
if partial:
    print(
        "[pre-commit] 检测到文件只有部分行暂存，自动格式化会覆盖未暂存改动：\n"
        f"  {', '.join(partial)}\n"
        "[pre-commit] 请先完整 git add 该文件，或用 --no-verify 提交后再手动格式化。"
    )
    sys.exit(1)

print(f"ruff format: {', '.join(staged)}")

# Format staged files (working tree already matches the index for these).
fmt = subprocess.run([*RUFF, "format", *staged], cwd=REPO_ROOT)
if fmt.returncode != 0:
    print("[pre-commit] ruff format failed, check output above")
    sys.exit(1)

# Only re-stage files that ruff actually modified
diff_result = subprocess.run(
    ["git", "diff", "--name-only", "--", *staged],
    capture_output=True,
    text=True,
    cwd=REPO_ROOT,
)
if diff_result.returncode == 0 and diff_result.stdout.strip():
    modified = [f for f in diff_result.stdout.splitlines() if f]
    subprocess.run(["git", "add", *modified], cwd=REPO_ROOT)

# Lint check (only errors, ignore warnings like F811)
check = subprocess.run([*RUFF, "check", "--select", "F,E,I", *staged], cwd=REPO_ROOT)
if check.returncode != 0:
    print("[pre-commit] ruff check failed - fix errors above before committing")
    sys.exit(1)
