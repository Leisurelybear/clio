"""Tests for clio/ui/services/file_service.py."""

from __future__ import annotations

from pathlib import Path

import yaml

from clio.ui.services.file_service import (
    _coerce_config_types,
    _create_project_yaml,
    _find_compressed_for_original,
    _find_original_for_compressed,
    _find_texts_dirs,
    _is_safe_basename,
    _save_atomic,
)

# ===================== _is_safe_basename =====================


class TestIsSafeBasename:
    def test_empty_string(self):
        assert _is_safe_basename("") is False

    def test_valid_basename(self):
        assert _is_safe_basename("001_GL010695.mp4") is True

    def test_too_long(self):
        assert _is_safe_basename("a" * 201) is False

    def test_contains_slash(self):
        assert _is_safe_basename("foo/bar.mp4") is False

    def test_contains_backslash(self):
        assert _is_safe_basename("foo\\bar.mp4") is False

    def test_contains_dotdot(self):
        assert _is_safe_basename("..mp4") is False
        assert _is_safe_basename("foo/../bar.mp4") is False

    def test_control_chars(self):
        assert _is_safe_basename("foo\x00bar.mp4") is False

    def test_cjk_punctuation_accepted(self):
        assert _is_safe_basename("002：巴黎.mp4") is True


# ===================== _find_texts_dirs =====================


class TestFindTextsDirs:
    def test_empty_dir_returns_empty(self, tmp_path: Path):
        out = tmp_path / "output"
        out.mkdir()
        assert _find_texts_dirs(out) == []

    def test_finds_texts_dir(self, tmp_path: Path):
        out = tmp_path / "output"
        out.mkdir()
        texts = out / "texts"
        texts.mkdir()
        result = _find_texts_dirs(out)
        assert len(result) == 1
        assert result[0] == texts

    def test_finds_texts_with_suffix(self, tmp_path: Path):
        out = tmp_path / "output"
        out.mkdir()
        (out / "texts - Paris").mkdir()
        result = _find_texts_dirs(out)
        assert len(result) == 1

    def test_ignores_texts_backup(self, tmp_path: Path):
        out = tmp_path / "output"
        out.mkdir()
        (out / "texts_backup").mkdir()
        assert _find_texts_dirs(out) == []

    def test_ignores_non_texts_dir(self, tmp_path: Path):
        out = tmp_path / "output"
        out.mkdir()
        (out / "compressed").mkdir()
        (out / "scripts").mkdir()
        assert _find_texts_dirs(out) == []

    def test_nonexistent_output_returns_empty(self):
        assert _find_texts_dirs(Path("/nonexistent")) == []


# ===================== _save_atomic =====================


class TestSaveAtomic:
    def test_writes_file(self, tmp_path: Path):
        target = tmp_path / "test.txt"
        _save_atomic(target, b"hello world")
        assert target.read_bytes() == b"hello world"

    def test_creates_parent_dirs(self, tmp_path: Path):
        target = tmp_path / "sub" / "nested" / "test.txt"
        _save_atomic(target, b"data")
        assert target.read_bytes() == b"data"

    def test_backup_created(self, tmp_path: Path):
        target = tmp_path / "test.txt"
        target.write_bytes(b"original")
        _save_atomic(target, b"updated")
        assert target.read_bytes() == b"updated"
        bak = target.with_suffix(".txt.bak")
        assert bak.read_bytes() == b"original"

    def test_backup_overwritten_on_subsequent_saves(self, tmp_path: Path):
        target = tmp_path / "test.txt"
        target.write_bytes(b"original")
        _save_atomic(target, b"updated")
        _save_atomic(target, b"final")
        bak = target.with_suffix(".txt.bak")
        assert bak.read_bytes() == b"updated"

    def test_tmp_file_cleaned(self, tmp_path: Path):
        target = tmp_path / "test.txt"
        _save_atomic(target, b"data")
        tmp_files = list(tmp_path.glob("*.tmp.*"))
        assert tmp_files == []
        # Also no leftover exclusive-temp style names
        assert list(tmp_path.glob(".*.tmp")) == []

    def test_planted_symlink_temp_not_followed(self, tmp_path: Path, monkeypatch):
        """GAP-P1-02: must not follow a planted symlink at the temp path."""
        import pytest

        target = tmp_path / "out.txt"
        victim = tmp_path / "victim.bin"
        victim.write_bytes(b"keep me")

        # Plant at the legacy predictable name (.txt.tmp.<4-byte-hex>).
        legacy_hex = (b"\x11" * 4).hex()
        legacy_planted = target.with_suffix(target.suffix + f".tmp.{legacy_hex}")
        try:
            legacy_planted.symlink_to(victim)
        except OSError:
            pytest.skip("symlink creation not permitted on this host")

        # Also plant at exclusive-random naming so the fix path is covered.
        excl_hex = (b"\x11" * 8).hex()
        excl_planted = tmp_path / f".out.txt.{excl_hex}.tmp"
        try:
            excl_planted.symlink_to(victim)
        except OSError:
            pytest.skip("symlink creation not permitted on this host")

        calls = {"n": 0}

        def colliding_then_safe(n: int) -> bytes:
            calls["n"] += 1
            # First attempt collides with the planted name for both schemes.
            if calls["n"] == 1:
                return b"\x11" * n
            return b"\x22" * n

        monkeypatch.setattr("clio.utils.os.urandom", colliding_then_safe)
        _save_atomic(target, b"new content")
        assert victim.read_bytes() == b"keep me"
        assert target.read_bytes() == b"new content"

    def test_existing_bak_not_clobbered_by_corrupt_current(self, tmp_path: Path):
        """GAP-P2-02: saving must rotate bak aside, not overwrite it with corrupt current."""
        target = tmp_path / "data.json"
        bak = tmp_path / "data.json.bak"
        target.write_bytes(b"{corrupt")
        bak.write_bytes(b'{"projects":["good"]}')
        _save_atomic(target, b'{"projects":[]}')
        assert target.read_bytes() == b'{"projects":[]}'
        survivors = list(tmp_path.glob("data.json.bak*"))
        assert any(p.read_bytes() == b'{"projects":["good"]}' for p in survivors)

    def test_does_not_delete_lone_bak_before_create(self, tmp_path: Path):
        """GAP-P2-02: creating a new primary must not unlink the only backup first."""
        target = tmp_path / "data.json"
        bak = tmp_path / "data.json.bak"
        bak.write_bytes(b'{"projects":["only-good"]}')
        _save_atomic(target, b'{"projects":["new"]}')
        assert target.read_bytes() == b'{"projects":["new"]}'
        assert bak.exists()
        assert bak.read_bytes() == b'{"projects":["only-good"]}'


# ===================== _find_original_for_compressed =====================


class TestFindOriginalForCompressed:
    def test_exact_match(self, tmp_path: Path):
        (tmp_path / "GL010695.MP4").write_bytes(b"")
        result = _find_original_for_compressed("001_GL010695", tmp_path)
        assert result is not None and Path(result).name == "GL010695.MP4"

    def test_case_insensitive(self, tmp_path: Path):
        (tmp_path / "gl010695.mp4").write_bytes(b"")
        result = _find_original_for_compressed("001_GL010695", tmp_path)
        assert result is not None and Path(result).name.lower() == "gl010695.mp4"

    def test_no_match(self, tmp_path: Path):
        result = _find_original_for_compressed("001_NOFILE", tmp_path)
        assert result is None

    def test_no_underscore(self, tmp_path: Path):
        result = _find_original_for_compressed("NOUNDERSCORE", tmp_path)
        assert result is None

    def test_nonexistent_input_dir(self, tmp_path: Path):
        result = _find_original_for_compressed("001_GL010695", tmp_path / "nonexistent")
        assert result is None

    def test_segNN_fallback(self, tmp_path: Path):
        (tmp_path / "GL010695.MP4").write_bytes(b"")
        result = _find_original_for_compressed("001_GL010695_seg01", tmp_path)
        assert result is not None and Path(result).name == "GL010695.MP4"

    def test_segNN_no_original(self, tmp_path: Path):
        # suffix has _seg01 but no original matches
        (tmp_path / "OTHER.MP4").write_bytes(b"")
        result = _find_original_for_compressed("001_GL010695_seg01", tmp_path)
        assert result is None

    def test_first_underscore_suffix(self, tmp_path: Path):
        # Does the split use the thing after the FIRST underscore?
        (tmp_path / "bar.MP4").write_bytes(b"")
        result = _find_original_for_compressed("foo_bar_seg01", tmp_path)
        assert result is not None and Path(result).name == "bar.MP4"


# ===================== _find_compressed_for_original =====================


class TestFindCompressedForOriginal:
    def test_exact_match(self, tmp_path: Path):
        comp = tmp_path / "compressed"
        comp.mkdir()
        (comp / "001_GL010695.mp4").write_bytes(b"")
        result = _find_compressed_for_original("GL010695", comp)
        assert result == [("001_GL010695.mp4", "001")]

    def test_no_match(self, tmp_path: Path):
        comp = tmp_path / "compressed"
        comp.mkdir()
        result = _find_compressed_for_original("GL010695", comp)
        assert result is None

    def test_nonexistent_comp_dir(self, tmp_path: Path):
        result = _find_compressed_for_original("GL010695", tmp_path / "nonexistent")
        assert result is None

    def test_multiple_segments_sorted(self, tmp_path: Path):
        comp = tmp_path / "compressed"
        comp.mkdir()
        (comp / "003_GL010695_seg02.mp4").write_bytes(b"")
        (comp / "001_GL010695_seg01.mp4").write_bytes(b"")
        (comp / "002_GL010695_seg03.mp4").write_bytes(b"")
        result = _find_compressed_for_original("GL010695", comp)
        assert result is not None
        # sorted by index string lexicographically
        assert result[0] == ("001_GL010695_seg01.mp4", "001")
        assert result[1] == ("002_GL010695_seg03.mp4", "002")
        assert result[2] == ("003_GL010695_seg02.mp4", "003")

    def test_exact_match_takes_precedence(self, tmp_path: Path):
        """If exact match exists alongside segments, return only the exact."""
        comp = tmp_path / "compressed"
        comp.mkdir()
        (comp / "001_GL010695.mp4").write_bytes(b"")
        (comp / "002_GL010695_seg01.mp4").write_bytes(b"")
        result = _find_compressed_for_original("GL010695", comp)
        assert result == [("001_GL010695.mp4", "001")]

    def test_case_insensitive_needle(self, tmp_path: Path):
        comp = tmp_path / "compressed"
        comp.mkdir()
        (comp / "001_gl010695.mp4").write_bytes(b"")
        result = _find_compressed_for_original("GL010695", comp)
        assert result == [("001_gl010695.mp4", "001")]

    def test_seg_prefix_not_false_match(self, tmp_path: Path):
        """A file with '_seg' in name but not followed by digits should not match."""
        comp = tmp_path / "compressed"
        comp.mkdir()
        (comp / "001_GL010695_segment.mp4").write_bytes(b"")
        result = _find_compressed_for_original("GL010695", comp)
        assert result is None

    def test_seg_prefix_with_trailing_text_not_digits(self, tmp_path: Path):
        """_segXXX where XXX has non-digit should not match."""
        comp = tmp_path / "compressed"
        comp.mkdir()
        (comp / "001_GL010695_seg01a.mp4").write_bytes(b"")
        result = _find_compressed_for_original("GL010695", comp)
        assert result is None

    def test_ignores_non_video_ext(self, tmp_path: Path):
        comp = tmp_path / "compressed"
        comp.mkdir()
        (comp / "001_GL010695.txt").write_bytes(b"")
        result = _find_compressed_for_original("GL010695", comp)
        assert result is None

    def test_ignores_no_underscore(self, tmp_path: Path):
        comp = tmp_path / "compressed"
        comp.mkdir()
        (comp / "GL010695.mp4").write_bytes(b"")
        result = _find_compressed_for_original("GL010695", comp)
        assert result is None


# ===================== _coerce_config_types =====================


class TestCoerceConfigTypes:
    def test_none_ref_returns_new(self):
        assert _coerce_config_types("foo", None) == "foo"

    def test_bool_true(self):
        assert _coerce_config_types("true", True) is True

    def test_bool_false(self):
        assert _coerce_config_types("false", True) is False

    def test_bool_1(self):
        assert _coerce_config_types("1", True) is True

    def test_bool_0(self):
        assert _coerce_config_types("0", True) is False

    def test_bool_yes(self):
        assert _coerce_config_types("yes", True) is True

    def test_int(self):
        assert _coerce_config_types("42", 0) == 42

    def test_int_none(self):
        assert _coerce_config_types(None, 0) is None

    def test_int_invalid(self):
        assert _coerce_config_types("abc", 0) == "abc"

    def test_float(self):
        assert _coerce_config_types("3.14", 0.0) == 3.14

    def test_float_none(self):
        assert _coerce_config_types(None, 0.0) is None

    def test_float_invalid(self):
        result = _coerce_config_types("abc", 0.0)
        assert result == "abc"

    def test_str_already_str(self):
        assert _coerce_config_types("hello", "") == "hello"

    def test_str_from_int(self):
        assert _coerce_config_types(42, "") == "42"

    def test_list_coercion(self):
        ref = [0]
        new = ["1", "2", "3"]
        assert _coerce_config_types(new, ref) == [1, 2, 3]

    def test_list_empty(self):
        assert _coerce_config_types([], [0]) == []

    def test_dict_coercion(self):
        ref = {"a": 0, "b": ""}
        new = {"a": "42", "b": "hello"}
        result = _coerce_config_types(new, ref)
        assert result == {"a": 42, "b": "hello"}

    def test_dict_extra_keys(self):
        ref = {"a": 0}
        new = {"a": "42", "extra": "keep"}
        result = _coerce_config_types(new, ref)
        assert "extra" in result

    def test_dict_missing_ref_key(self):
        ref = {"a": 0, "b": 0}
        new = {"a": "42"}
        result = _coerce_config_types(new, ref)
        assert "b" not in result  # not in new, not in result


# ===================== _create_project_yaml =====================


class TestCreateProjectYaml:
    def test_no_config_path_returns_none(self, tmp_path: Path):
        result = _create_project_yaml(tmp_path, None, tmp_path / "output")
        assert result is None

    def test_missing_config_returns_none(self, tmp_path: Path):
        result = _create_project_yaml(tmp_path, tmp_path / "nonexistent.yaml", tmp_path / "output")
        assert result is None

    def test_creates_yaml_from_template(self, tmp_path: Path):
        config = tmp_path / "config.yaml"
        config.write_text(
            yaml.dump(
                {
                    "paths": {"input_dir": "/fake/input", "output_dir": "/fake/output"},
                    "compress": {"target_size_mb": 5},
                }
            ),
            encoding="utf-8",
        )
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        proj_out = tmp_path / "project_output"
        result = _create_project_yaml(proj_dir, config, proj_out)
        assert result is not None
        assert result == proj_dir / "project.yaml"
        data = yaml.safe_load(result.read_text(encoding="utf-8"))
        assert "input_dir" not in data.get("paths", {})
        assert "recursive" not in data.get("paths", {})
        assert data["paths"]["output_dir"] == str(proj_out.resolve())
        assert data["compress"]["target_size_mb"] == 5

    def test_existing_project_yaml_not_overwritten(self, tmp_path: Path):
        config = tmp_path / "config.yaml"
        config.write_text(
            yaml.dump({"paths": {"input_dir": "/fake/input", "output_dir": "/fake/output"}}),
            encoding="utf-8",
        )
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        existing = proj_dir / "project.yaml"
        existing.write_text("existing: true", encoding="utf-8")
        result = _create_project_yaml(proj_dir, config, tmp_path / "output")
        assert result == existing
        assert yaml.safe_load(existing.read_text(encoding="utf-8")) == {"existing": True}

    def test_ai_context_defaults_empty(self, tmp_path: Path):
        config = tmp_path / "config.yaml"
        config.write_text(
            yaml.dump({"paths": {"input_dir": "/fake/input", "output_dir": "/fake/output"}}),
            encoding="utf-8",
        )
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        result = _create_project_yaml(proj_dir, config, tmp_path / "output")
        data = yaml.safe_load(result.read_text(encoding="utf-8"))
        assert data.get("ai", {}).get("context") == ""

    def test_strips_global_fields(self, tmp_path: Path):
        """Verify ai.providers (global-only) is NOT written to project.yaml."""
        config = tmp_path / "config.yaml"
        config.write_text(
            yaml.dump(
                {
                    "paths": {
                        "input_dir": "/in",
                        "output_dir": "/out",
                        "ffmpeg": "C:/ffmpeg.exe",
                        "logs_dir": "./logs",
                    },
                    "ai": {
                        "providers": {
                            "gemini": {"type": "gemini", "api_key_env": "GEMINI_API_KEY"},
                        },
                        "context": "test context",
                    },
                    "compress": {"fps": 15, "codec": "libx264", "target_size_mb": 10, "crf": 32},
                }
            ),
            encoding="utf-8",
        )
        proj_dir = tmp_path / "project2"
        proj_dir.mkdir()
        result = _create_project_yaml(proj_dir, config, tmp_path / "output")
        data = yaml.safe_load(result.read_text(encoding="utf-8"))
        # Project fields should survive; input_dir is no longer written
        assert "input_dir" not in data.get("paths", {})
        assert "recursive" not in data.get("paths", {})
        assert data["compress"]["target_size_mb"] == 10
        assert data["ai"]["context"] == "test context"
        # Global-only fields should be stripped
        assert "providers" not in data.get("ai", {})
        assert "ffmpeg" not in data.get("paths", {})
        assert "logs_dir" not in data.get("paths", {})
        assert "fps" not in data.get("compress", {})
        assert "codec" not in data.get("compress", {})
