"""Regression tests: .env files saved with a UTF-8 BOM keep their first key."""

from __future__ import annotations

import os
from pathlib import Path

from clio.config.loader import _load_dotenv, parse_dotenv
from clio.credentials.stores import DotenvStore

BOM = "\ufeff"


def test_parse_dotenv_ignores_leading_bom():
    assert parse_dotenv(BOM + "GEMINI_API_KEY=abc\nOTHER=1\n") == {"GEMINI_API_KEY": "abc", "OTHER": "1"}


def test_load_dotenv_reads_first_key_of_bom_file(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLIO_FIX_BOM_KEY", raising=False)
    (tmp_path / ".env").write_bytes((BOM + "CLIO_FIX_BOM_KEY=abc\n").encode("utf-8"))

    _load_dotenv(tmp_path)

    assert os.environ.get("CLIO_FIX_BOM_KEY") == "abc"


def test_dotenv_store_reads_and_rewrites_bom_file(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_bytes((BOM + "GEMINI_API_KEY=old\nOTHER=1\n").encode("utf-8"))
    store = DotenvStore(env_path)

    assert store.get("GEMINI_API_KEY") == "old"

    store.set("GEMINI_API_KEY", "new")

    saved = env_path.read_bytes().decode("utf-8")
    assert not saved.startswith(BOM)
    assert saved.count("GEMINI_API_KEY=") == 1
    assert store.get("GEMINI_API_KEY") == "new"
    assert store.get("OTHER") == "1"
