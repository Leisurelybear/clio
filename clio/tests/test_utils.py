"""Tests for clio/utils.py — pure utility functions."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from clio.utils import (
    _secret_has_insecure_perms,
    extract_json,
    find_videos,
    format_index,
    mask_if_looks_like_key,
    resolve_binary,
    sanitize_name,
    with_retry,
    write_secret_atomic,
)

# ── extract_json ────────────────────────────────────────────────────


class TestExtractJson:
    def test_plain_json_object(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_json_with_surrounding_text(self):
        text = 'Some prefix\n```json\n{"a": 1}\n```\nsuffix'
        assert extract_json(text) == {"a": 1}

    def test_nested_json(self):
        text = '{"a": {"b": [1, 2, 3]}}'
        assert extract_json(text) == {"a": {"b": [1, 2, 3]}}

    def test_empty_object(self):
        assert extract_json("{}") == {}

    def test_json_with_unicode(self):
        assert extract_json('{"name": "巴黎"}') == {"name": "巴黎"}

    def test_raises_on_invalid_input(self):
        with pytest.raises(ValueError):
            extract_json("not json at all no braces")

    def test_raises_on_empty_text(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_brace_in_string_extracted_correctly(self):
        text = '{"key": "value with { inside"}'
        assert extract_json(text) == {"key": "value with { inside"}

    def test_trailing_comma_in_object(self):
        assert extract_json('{"a": 1,}') == {"a": 1}

    def test_trailing_comma_in_nested(self):
        text = '{"a": {"b": [1, 2,],},}'
        assert extract_json(text) == {"a": {"b": [1, 2]}}

    def test_trailing_comma_with_surrounding_text(self):
        text = '一些文字\n```\n{"a": 1,}\n```\n结尾'
        assert extract_json(text) == {"a": 1}

    def test_no_valid_json_after_comma_fix_raises(self):
        with pytest.raises(ValueError):
            extract_json("{invalid,}")

    def test_raises_on_broken_json_that_even_comma_fix_cant_save(self):
        with pytest.raises(ValueError):
            extract_json("{a: 1}")

    def test_truncated_plan_json_is_repaired(self):
        """Model hit max_tokens mid-string: close open string + brackets."""
        text = (
            "{\n"
            '  "day_title": "巴黎·戴高乐",\n'
            '  "theme": "从机场到市区",\n'
            '  "total_estimated_sec": 179,\n'
            '  "sequence": [\n'
            "    {\n"
            '      "index": "001",\n'
            '      "title": "机舱",\n'
            '      "reason": "开场",\n'
            '      "use_timeline": "00:00-00:15",\n'
            '      "voiceover_hint": "飞机落地"\n'
            "    },\n"
            "    {\n"
            '      "index": "002",\n'
            '      "title": "列车",\n'
            '      "reason": "转场",\n'
            '      "use_timeline": "00:00-00:47",\n'
            '      "voiceover_hi'
        )
        data = extract_json(text)
        assert data["day_title"] == "巴黎·戴高乐"
        assert data["total_estimated_sec"] == 179
        assert len(data["sequence"]) == 1
        assert data["sequence"][0]["index"] == "001"

    def test_truncated_after_first_object_no_closing_brace(self):
        text = '{"a": 1, "b": [1, 2'
        data = extract_json(text)
        assert data == {"a": 1, "b": [1, 2]}


# ── mask_if_looks_like_key ──────────────────────────────────────────


class TestMaskIfLooksLikeKey:
    def test_empty_string(self):
        assert mask_if_looks_like_key("") == ""

    def test_sk_short(self):
        assert mask_if_looks_like_key("sk-") == "sk-"

    def test_sk_long(self):
        result = mask_if_looks_like_key("sk-abc123def456")
        assert result == "sk-a***f456"  # first 4 + *** + last 4
        assert len(result) < 20

    def test_aiza_key(self):
        result = mask_if_looks_like_key("AIzaSyFakeKeyForTestPurposesOnly123")
        assert result == "AIza***y123"  # first 4 + *** + last 4

    def test_ghp_token(self):
        result = mask_if_looks_like_key("ghp_xxxxxxxxxxxxxxxxxxxx")
        assert result.startswith("ghp_")
        assert "***" in result

    def test_long_string_without_prefix(self):
        result = mask_if_looks_like_key("a" * 40)
        assert result == f"{'a' * 6}***{'a' * 4}"

    def test_short_string_without_prefix(self):
        assert mask_if_looks_like_key("hello") == "hello"

    def test_short_string_with_spaces(self):
        assert (
            mask_if_looks_like_key("a normal sentence without key patterns") == "a normal sentence without key patterns"
        )


# ── sanitize_name ───────────────────────────────────────────────────


class TestSanitizeName:
    def test_removes_special_chars(self):
        assert sanitize_name("file:name test") == "filename_test"

    def test_replaces_whitespace(self):
        assert sanitize_name("my clip name") == "my_clip_name"

    def test_truncates_long(self):
        long_name = "a" * 100
        assert len(sanitize_name(long_name)) == 40

    def test_empty_fallback(self):
        assert sanitize_name("") == "clip"

    def test_trim_whitespace(self):
        result = sanitize_name("  hello  ")
        assert result == "hello"
        assert "_" not in result  # single word, no inner spaces after strip

    def test_chinese_chars_preserved(self):
        assert sanitize_name("巴黎铁塔") == "巴黎铁塔"


# ── format_index ────────────────────────────────────────────────────


class TestFormatIndex:
    def test_basic(self):
        assert format_index(1, 3) == "001"

    def test_width_2(self):
        assert format_index(5, 2) == "05"

    def test_large_index(self):
        assert format_index(100, 3) == "100"

    def test_width_4(self):
        assert format_index(42, 4) == "0042"


# ── find_videos ─────────────────────────────────────────────────────


class TestFindVideos:
    def test_empty_directory(self, tmp_path):
        assert find_videos(tmp_path) == []

    def test_finds_mp4_files(self, tmp_path):
        (tmp_path / "clip1.mp4").write_text("fake video")
        (tmp_path / "clip2.mp4").write_text("fake video")
        result = find_videos(tmp_path)
        assert len(result) == 2
        assert all(p.suffix == ".mp4" for p in result)

    def test_ignores_non_video_files(self, tmp_path):
        (tmp_path / "notes.txt").write_text("text")
        (tmp_path / "image.jpg").write_text("image")
        assert find_videos(tmp_path) == []

    def test_recursive_finds_nested(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (tmp_path / "root.mp4").write_text("fake")
        (sub / "nested.mov").write_text("fake")
        result = find_videos(tmp_path, recursive=True)
        assert len(result) == 2

    def test_non_recursive_ignores_nested(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "nested.mp4").write_text("fake")
        assert find_videos(tmp_path) == []

    def test_nonexistent_directory_raises(self):
        with pytest.raises(NotADirectoryError):
            find_videos(Path("/nonexistent/path"))

    def test_case_insensitive_extensions(self, tmp_path):
        (tmp_path / "clip.MP4").write_text("fake")
        (tmp_path / "clip.MOV").write_text("fake")
        assert len(find_videos(tmp_path)) == 2

    def test_multiple_extensions(self, tmp_path):
        for ext in [".mp4", ".mov", ".avi", ".mkv"]:
            (tmp_path / f"file{ext}").write_text("fake")
        assert len(find_videos(tmp_path)) == 4


# ── with_retry ──────────────────────────────────────────────────


class TestWithRetry:
    def test_success_first_try(self):
        assert with_retry(lambda: 42, attempts=3, retry_on=(ValueError,), what="test") == 42

    def test_retry_then_succeed(self):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 2:
                raise ValueError("temporary")
            return "ok"

        assert with_retry(fn, attempts=3, retry_on=(ValueError,), what="test") == "ok"
        assert len(calls) == 2

    def test_exhaust_retries(self):
        with pytest.raises(ValueError, match="fail"):
            with_retry(
                lambda: (_ for _ in ()).throw(ValueError("fail")), attempts=2, retry_on=(ValueError,), what="test"
            )

    def test_non_retryable_exception(self):
        with pytest.raises(TypeError):
            with_retry(lambda: (_ for _ in ()).throw(TypeError("bad")), attempts=3, retry_on=(ValueError,), what="test")

    def test_zero_attempts(self):
        calls = []

        def fn():
            calls.append(1)
            raise ValueError("fail")

        with pytest.raises(ValueError):
            with_retry(fn, attempts=1, retry_on=(ValueError,), what="test")
        assert len(calls) == 1


# ── discover_ffmpeg_bin / resolve_binary ────────────────────────


class TestResolveBinary:
    def test_configured_path_exists(self, tmp_path):
        exe = tmp_path / "ffmpeg.exe"
        exe.write_text("fake")
        assert resolve_binary(str(exe), "ffmpeg") == str(exe)

    def test_configured_path_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            resolve_binary(str(tmp_path / "nonexistent.exe"), "ffmpeg")

    def test_empty_config_falls_back(self):
        with pytest.raises(FileNotFoundError):
            resolve_binary("", "ffmpeg_nonexistent_tool_xyz")


# ── write_secret_atomic (GAP-P1-11) ────────────────────────────────


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits not applicable on Windows")
class TestWriteSecretAtomic:
    def test_new_file_created_owner_only(self, tmp_path):
        path = tmp_path / ".env"
        write_secret_atomic(path, "KEY=val\n")
        assert path.read_text(encoding="utf-8") == "KEY=val\n"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_overwrites_owner_only_existing(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("OLD=1\n")
        os.chmod(path, 0o600)
        write_secret_atomic(path, "NEW=2\n")
        assert path.read_text(encoding="utf-8") == "NEW=2\n"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_refuses_overwrite_of_group_accessible(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("OLD=1\n")
        os.chmod(path, 0o640)
        with pytest.raises(PermissionError):
            write_secret_atomic(path, "NEW=2\n")
        assert path.read_text(encoding="utf-8") == "OLD=1\n"

    def test_refuses_overwrite_of_world_writable(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("OLD=1\n")
        os.chmod(path, 0o666)
        with pytest.raises(PermissionError):
            write_secret_atomic(path, "NEW=2\n")
        assert path.read_text(encoding="utf-8") == "OLD=1\n"

    def test_no_leftover_temp_file(self, tmp_path):
        path = tmp_path / ".env"
        write_secret_atomic(path, "KEY=val\n")
        leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits not applicable on Windows")
class TestSecretHasInsecurePerms:
    def test_missing_file_not_insecure(self, tmp_path):
        assert _secret_has_insecure_perms(tmp_path / ".env") is False

    def test_600_not_insecure(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("K=v\n")
        os.chmod(path, 0o600)
        assert _secret_has_insecure_perms(path) is False

    def test_644_is_insecure(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("K=v\n")
        os.chmod(path, 0o644)
        assert _secret_has_insecure_perms(path) is True

    def test_640_is_insecure(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("K=v\n")
        os.chmod(path, 0o640)
        assert _secret_has_insecure_perms(path) is True


class TestValidateWithinRoot:
    def test_normal_path_inside_root(self, tmp_path: Path):
        from clio.utils import validate_within_root

        root = tmp_path / "root"
        root.mkdir()
        child = root / "child" / "file.txt"
        child.parent.mkdir(parents=True)
        child.write_text("test")

        result = validate_within_root(child, root)
        assert result == child.resolve()

    def test_symlink_in_path_blocked(self, tmp_path: Path):
        """GAP-P1-04: symlink component in path must be rejected."""
        from clio.utils import validate_within_root

        root = tmp_path / "root"
        root.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        (other / "secret.txt").write_text("leaked")

        link = root / "link"
        link.symlink_to(other)

        with pytest.raises(ValueError, match="symlink"):
            validate_within_root(link / "secret.txt", root)

    def test_parent_escape_blocked(self, tmp_path: Path):
        """GAP-P1-04: path escaping root via ../ must be rejected."""
        from clio.utils import validate_within_root

        root = tmp_path / "root"
        root.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        (other / "secret.txt").write_text("leaked")

        with pytest.raises(ValueError, match="escapes root"):
            validate_within_root(root.parent / "other" / "secret.txt", root)

    def test_root_is_symlink_blocked(self, tmp_path: Path):
        """GAP-P1-04: root itself being a symlink must be rejected."""
        from clio.utils import validate_within_root

        real_root = tmp_path / "real"
        real_root.mkdir()
        link_root = tmp_path / "link"
        link_root.symlink_to(real_root)
        (real_root / "secret.txt").write_text("leaked")

        with pytest.raises(ValueError, match="symlink"):
            validate_within_root(link_root / "secret.txt", link_root)

    def test_relative_path_resolved_correctly(self, tmp_path: Path):
        from clio.utils import validate_within_root

        root = tmp_path / "root"
        root.mkdir()
        child = root / "child.txt"
        child.write_text("test")

        result = validate_within_root(tmp_path / "root" / "child.txt", tmp_path)
        assert result == child.resolve()
