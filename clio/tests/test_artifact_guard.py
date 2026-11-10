"""Tests for artifact edit markers and backups."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from clio.artifact_guard import (
    ArtifactOverwriteConfirmationRequired,
    artifact_requires_overwrite_confirmation,
    backup_artifacts,
    clear_user_edited,
    is_user_edited,
    mark_user_edited,
    read_user_edited,
)


def test_mark_and_clear_user_edited():
    data = {"voiceover": "edited"}

    mark_user_edited(data, edited_at="2026-11-10T00:00:00Z")

    assert is_user_edited(data) is True
    assert data["_user_edited_at"] == "2026-11-10T00:00:00Z"
    assert clear_user_edited(data) is data
    assert "_user_edited" not in data
    assert "_user_edited_at" not in data


def test_read_user_edited_handles_missing_invalid_and_marked_files(tmp_path: Path):
    assert read_user_edited(tmp_path / "missing.json") is False
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{invalid", encoding="utf-8")
    assert read_user_edited(invalid) is False
    marked = tmp_path / "marked.json"
    marked.write_text(json.dumps({"_user_edited": True}), encoding="utf-8")
    assert read_user_edited(marked) is True


def test_overwrite_guard_is_conservative_for_existing_unparseable_artifacts(tmp_path: Path):
    assert artifact_requires_overwrite_confirmation(tmp_path / "missing.json") is False

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{invalid", encoding="utf-8")
    assert artifact_requires_overwrite_confirmation(invalid) is True

    non_object = tmp_path / "list.json"
    non_object.write_text("[]", encoding="utf-8")
    assert artifact_requires_overwrite_confirmation(non_object) is True

    marked = tmp_path / "marked.json"
    marked.write_text(json.dumps({"_user_edited": True}), encoding="utf-8")
    assert artifact_requires_overwrite_confirmation(marked) is True

    ordinary = tmp_path / "ordinary.json"
    ordinary.write_text(json.dumps({"title": "generated"}), encoding="utf-8")
    assert artifact_requires_overwrite_confirmation(ordinary) is False


def test_backup_artifacts_uses_unique_names_and_skips_missing(tmp_path: Path):
    first = tmp_path / "plan.json"
    first.write_bytes(b"first")
    missing = tmp_path / "missing.json"

    backups = backup_artifacts([first, missing, first])

    assert len(backups) == 1
    assert backups[0].read_bytes() == b"first"
    assert backups[0].name.startswith("plan.json.bak.")
    assert backups[0] != first


def test_backup_artifacts_never_overwrites_existing_backup(tmp_path: Path):
    artifact = tmp_path / "plan.json"
    artifact.write_bytes(b"payload")

    with patch("clio.artifact_guard.uuid.uuid4", return_value=type("U", (), {"hex": "12345678"})()):
        with patch("clio.artifact_guard.time.time_ns", side_effect=[99, 100]):
            first = backup_artifacts([artifact])[0]
            second = backup_artifacts([artifact])[0]

    assert first != second
    assert first.exists()
    assert second.exists()


def test_backup_failure_prevents_followup_overwrite(tmp_path: Path):
    artifact = tmp_path / "plan.json"
    artifact.write_bytes(b"original")

    with patch("clio.artifact_guard.os.fsync", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            backup_artifacts([artifact])

    assert artifact.read_bytes() == b"original"


def test_exception_exposes_artifact_paths(tmp_path: Path):
    artifact = tmp_path / "plan.json"
    exc = ArtifactOverwriteConfirmationRequired("confirm", [artifact])

    assert exc.artifacts == (artifact,)
