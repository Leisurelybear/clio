"""Tests for clio/schema.py — artifact schema versioning."""

from __future__ import annotations

import logging

from clio.schema import ARTIFACT_SCHEMA_VERSION, add_schema_version, check_schema_version


class TestAddSchemaVersion:
    def test_sets_current_version_in_place(self) -> None:
        data: dict = {"key": "value"}
        result = add_schema_version(data)
        assert result is data
        assert data["_schema_version"] == ARTIFACT_SCHEMA_VERSION

    def test_overwrites_stale_version(self) -> None:
        data: dict = {"_schema_version": 1}
        add_schema_version(data)
        assert data["_schema_version"] == ARTIFACT_SCHEMA_VERSION


class TestCheckSchemaVersion:
    def test_matching_version_returns_true(self, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            assert check_schema_version({"_schema_version": ARTIFACT_SCHEMA_VERSION}) is True
        assert caplog.records == []

    def test_no_version_field_returns_true_and_warns(self, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            assert check_schema_version({}) is True
        assert len(caplog.records) == 1
        assert "no _schema_version" in caplog.records[0].getMessage()

    def test_mismatched_version_returns_false_and_warns(self, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            assert check_schema_version({"_schema_version": 1}, label="analysis") is False
        assert len(caplog.records) == 1
        msg = caplog.records[0].getMessage()
        assert "analysis schema v1 != current" in msg

    def test_custom_label_used_in_warning(self, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            check_schema_version({}, label="plan")
        assert "plan has no _schema_version" in caplog.records[0].getMessage()

    def test_round_trip(self) -> None:
        data: dict = {"x": 1}
        add_schema_version(data)
        assert check_schema_version(data) is True
