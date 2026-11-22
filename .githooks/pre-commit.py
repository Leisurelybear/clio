#!/usr/bin/env python3
"""Pre-commit hook: auto-format staged .py files with ruff."""

import shutil
import subprocess
import sys
from contextlib import suppress
from pathlib import Path


def _reconfigure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        with suppress(OSError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")


_reconfigure_stdio()

REPO_ROOT = Path(__file__).resolve().parent.parent

# Locate ruff via project venv
ruff_candidates = [
    REPO_ROOT / ".venv" / "Scripts" / "ruff.exe",  # Windows
    REPO_ROOT / ".venv" / "bin" / "ruff",  # Unix
]
RUFF = next((p for p in ruff_candidates if p.is_file()), None)


def _skip(reason: str) -> None:
    # Loud on purpose: a silent skip let unformatted code reach CI's `ruff format --check`.
    print(
        f"[pre-commit] WARNING: {reason}; staged files were NOT formatted or linted.\n"
        "[pre-commit] CI runs `ruff format --check` and will fail on unformatted code.",
        file=sys.stderr,
    )
    sys.exit(0)


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
    if RUFF is None:
        # Last resort: a ruff on PATH (pipx / system install).
        path_ruff = shutil.which("ruff")
        if path_ruff:
            RUFF = [path_ruff]
        elif python.is_file():
            _skip("ruff not found in .venv or on PATH")
        else:
            _skip("no Python venv and no ruff on PATH")

if not isinstance(RUFF, list):
    RUFF = [str(RUFF)]

# Get staged .py files (excluding deletions; R keeps renamed-and-edited files).
# -z: NUL-separated, unquoted paths, so non-ASCII names are not escaped by core.quotepath.
result = subprocess.run(
    ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR", "--", "*.py"],
    capture_output=True,
    text=True,
    encoding="utf-8",
    cwd=REPO_ROOT,
)
if result.returncode != 0 or not result.stdout.strip("\0"):
    sys.exit(0)

staged = [f for f in result.stdout.split("\0") if f]
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
    ["git", "diff", "--name-only", "-z", "--", *staged],
    capture_output=True,
    text=True,
    encoding="utf-8",
    cwd=REPO_ROOT,
)
if diff_result.returncode == 0 and diff_result.stdout.strip("\0"):
    modified = [f for f in diff_result.stdout.split("\0") if f]
    add = subprocess.run(["git", "add", *modified], cwd=REPO_ROOT)
    if add.returncode != 0:
        print("[pre-commit] git add of the formatted files failed, check output above")
        sys.exit(1)

# Lint check (only errors, ignore warnings like F811)
check = subprocess.run([*RUFF, "check", "--select", "F,E,I", *staged], cwd=REPO_ROOT)
if check.returncode != 0:
    print("[pre-commit] ruff check failed - fix errors above before committing")
    sys.exit(1)
