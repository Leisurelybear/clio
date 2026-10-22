"""Guard: user-facing Python copy must resolve through the shared catalog.

Milestone 10 of docs/superpowers/plans/2026-10-22-i18n-completion-plan.md.

Two invariants are enforced here:

1. No module under ``clio/`` (outside the documented exclusions) may keep a
   Chinese string literal that is not explicitly allow-listed below. Comments
   and docstrings never count, because the scan walks the AST.
2. Every literal key passed to ``t()`` must resolve in the merged catalog, so a
   typo cannot silently ship a raw key to the user.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import clio.i18n as i18n

REPO_ROOT = Path(__file__).resolve().parents[2]
CJK = re.compile(r"[\u4e00-\u9fff]")

# Files that intentionally keep Chinese copy.
EXCLUDED_FILES = {
    "clio/config/descriptions.py",  # D3: zh-CN source of truth, mirrored to settings.desc.*
    "clio/i18n.py",
    "clio/prompts.py",  # AI prompts are not translated (plan Non-Goals)
}

# path -> sorted tuple of Chinese literals that are allowed to stay.
ALLOWED_LITERALS: dict[str, tuple[str, ...]] = {
    # AI prompt fragments and the persisted-data sentinel are not translated.
    "clio/analyze.py": (
        " 字。只按给出的素材范围估算，不要假设未提供的剪辑区间。\n",
        " 秒；按每秒约 2.5-3.5 个汉字，建议口播约 ",
        "## 背景与规范（请严格遵守）\n\n",
        "<必须从上面的素材列表原样选择>",
        "当前素材 timeline 覆盖时长约 ",
        "当前素材没有可解析的 timeline 时长，请勿虚构精确时长。\n",
        "日 vlog 标签: ",
        "未知",
        "（无）",
    ),
    "clio/analyze_windows.py": ("未知",),
}


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    i18n._reset_cache_for_tests()
    yield
    i18n._reset_cache_for_tests()


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _python_sources() -> list[Path]:
    sources = []
    for path in sorted((REPO_ROOT / "clio").rglob("*.py")):
        relative = _relative(path)
        if relative.startswith("clio/tests/"):
            continue
        if relative in EXCLUDED_FILES:
            continue
        sources.append(path)
    return sources


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                if isinstance(first.value.value, str):
                    ids.add(id(first.value))
    return ids


def _chinese_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = _docstring_ids(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            if CJK.search(node.value):
                found.append(node.value)
    return found


def _translation_keys(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    keys = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != "t":
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            keys.append(first.value)
    return keys


def test_no_untranslated_chinese_literals_outside_the_allow_list():
    actual = {
        _relative(path): tuple(sorted(set(_chinese_literals(path))))
        for path in _python_sources()
        if _chinese_literals(path)
    }
    expected = {path: tuple(sorted(set(values))) for path, values in ALLOWED_LITERALS.items()}

    unexpected = {
        path: sorted(set(values) - set(expected.get(path, ())))
        for path, values in actual.items()
        if set(values) - set(expected.get(path, ()))
    }
    stale = {
        path: sorted(set(values) - set(actual.get(path, ())))
        for path, values in expected.items()
        if set(values) - set(actual.get(path, ()))
    }

    assert not unexpected, f"new hard-coded Chinese copy: {unexpected}"
    assert not stale, f"allow-list entries are stale: {stale}"


def test_every_translated_key_resolves_in_the_catalog():
    merged = i18n._load_catalog("zh-CN")
    missing: dict[str, list[str]] = {}
    for path in _python_sources():
        for key in _translation_keys(path):
            if key not in merged:
                missing.setdefault(_relative(path), []).append(key)

    assert not missing, f"t() keys missing from the catalog: {missing}"
