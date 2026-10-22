"""Security and fallback tests for the managed FFmpeg installer."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import subprocess
import tarfile
import threading
import zipfile
from pathlib import Path

import pytest

from clio import ffmpeg_install as installer


def _zip(path: Path, entries: dict[str, bytes], *, symlink: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"target")
    return path


def _tar(path: Path, entries: dict[str, bytes], *, symlink: str | None = None) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for name, content in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        if symlink:
            info = tarfile.TarInfo(symlink)
            info.type = tarfile.SYMTYPE
            info.linkname = "/tmp/outside"
            archive.addfile(info)
    return path


def test_select_asset_platform_matrix():
    assert installer.select_asset("win32", "AMD64").archive.endswith("win64-gpl-9.0.zip")
    assert installer.select_asset("win32", "arm64").archive.endswith("winarm64-gpl-9.0.zip")
    assert installer.select_asset("linux", "x86_64").archive.endswith("linux64-gpl-9.0.tar.xz")
    assert installer.select_asset("linux", "aarch64").archive.endswith("linuxarm64-gpl-9.0.tar.xz")
    assert installer.select_asset("darwin", "arm64") is None
    assert installer.select_asset("linux", "i686") is None


def test_managed_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CLIO_FFMPEG_DIR", str(tmp_path / "managed"))
    assert installer.managed_install_root() == (tmp_path / "managed").resolve()


def test_parse_checksum_exact_match_only():
    digest = "a" * 64
    assert installer.parse_checksum(f"{digest}  ffmpeg.zip\n", "ffmpeg.zip") == digest
    assert installer.parse_checksum(f"{digest} *ffmpeg.zip\n", "ffmpeg.zip") == digest
    with pytest.raises(ValueError, match="not found"):
        installer.parse_checksum(f"{digest}  other.zip\n", "ffmpeg.zip")


@pytest.mark.parametrize("name", ["../evil", "/absolute", "a/../../evil", ""])
def test_archive_path_validation_rejects_escape(name):
    with pytest.raises(ValueError, match="unsafe archive path"):
        installer._safe_member_name(name)


def test_archive_entry_validation_rejects_symlinks_and_duplicates():
    with pytest.raises(ValueError, match="unsafe archive entry type"):
        installer._validate_entries([("bin/ffmpeg", "symlink")])
    with pytest.raises(ValueError, match="duplicate archive entry"):
        installer._validate_entries([("bin/ffmpeg", None), ("bin/ffmpeg", None)])


def test_extract_zip_only_writes_requested_binaries(tmp_path):
    archive = _zip(
        tmp_path / "ffmpeg.zip",
        {
            "ffmpeg-build/bin/ffmpeg.exe": b"ffmpeg-binary",
            "ffmpeg-build/bin/ffprobe.exe": b"ffprobe-binary",
            "ffmpeg-build/README.txt": b"ignored",
        },
    )
    dest = tmp_path / "out"

    ffmpeg, ffprobe = installer.extract_binaries(archive, dest, exe_name="ffmpeg.exe")

    assert ffmpeg.read_bytes() == b"ffmpeg-binary"
    assert ffprobe.read_bytes() == b"ffprobe-binary"
    assert not (dest / "README.txt").exists()


def test_extract_finds_linux_tar_binaries(tmp_path):
    archive = _tar(
        tmp_path / "ffmpeg.tar.gz",
        {"ffmpeg-build/bin/ffmpeg": b"linux-ffmpeg", "ffmpeg-build/bin/ffprobe": b"linux-ffprobe"},
    )
    ffmpeg, ffprobe = installer.extract_binaries(archive, tmp_path / "out", exe_name="ffmpeg")
    assert ffmpeg.read_bytes() == b"linux-ffmpeg"
    assert ffprobe.read_bytes() == b"linux-ffprobe"
    if os.name != "nt":
        assert ffmpeg.stat().st_mode & stat.S_IXUSR


def test_extract_rejects_tar_symlink(tmp_path):
    archive = _tar(
        tmp_path / "ffmpeg.tar.gz",
        {"ffmpeg-build/bin/ffmpeg": b"x", "ffmpeg-build/bin/ffprobe": b"y"},
        symlink="ffmpeg-build/bin/evil",
    )
    with pytest.raises(ValueError, match="unsafe archive entry type"):
        installer.extract_binaries(archive, tmp_path / "out", exe_name="ffmpeg")


def test_extract_rejects_duplicate_binaries(tmp_path):
    with zipfile.ZipFile(tmp_path / "ffmpeg.zip", "w") as archive:
        archive.writestr("one/bin/ffmpeg.exe", b"a")
        archive.writestr("one/bin/ffprobe.exe", b"b")
        archive.writestr("two/bin/ffmpeg.exe", b"c")
    with pytest.raises(ValueError, match="exactly one bin/ffmpeg.exe"):
        installer.extract_binaries(tmp_path / "ffmpeg.zip", tmp_path / "out", exe_name="ffmpeg.exe")


def test_download_requires_https():
    with pytest.raises(ValueError, match="HTTPS"):
        installer._download(
            "http://example.test/ffmpeg.zip", Path("unused"), max_bytes=1, reporter=None, cancel_event=None
        )


class _Response:
    def __init__(self, body: bytes, *, url: str = "https://example.test/file", length: int | None = None):
        self._body = io.BytesIO(body)
        self._url = url
        self.headers = {"Content-Length": str(len(body) if length is None else length)}

    def geturl(self):
        return self._url

    def read(self, size):
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_download_rejects_http_redirect_and_caps(monkeypatch, tmp_path):
    monkeypatch.setattr(
        installer.urllib.request, "urlopen", lambda request, timeout=30: _Response(b"x", url="http://evil")
    )
    with pytest.raises(ValueError, match="non-HTTPS"):
        installer._download("https://example.test/a", tmp_path / "a", max_bytes=100, reporter=None, cancel_event=None)

    monkeypatch.setattr(installer.urllib.request, "urlopen", lambda request, timeout=30: _Response(b"x", length=101))
    with pytest.raises(ValueError, match="too large"):
        installer._download("https://example.test/a", tmp_path / "a", max_bytes=100, reporter=None, cancel_event=None)


def test_download_writes_and_reports(monkeypatch, tmp_path):
    progress = []
    monkeypatch.setattr(installer.urllib.request, "urlopen", lambda request, timeout=30: _Response(b"abc"))
    dest = tmp_path / "file.bin"
    installer._download(
        "https://example.test/a",
        dest,
        max_bytes=100,
        reporter=lambda pct, message: progress.append((pct, message)),
        cancel_event=None,
    )
    assert dest.read_bytes() == b"abc"
    assert progress


def _fake_runner(cmd, **kwargs):
    return subprocess.CompletedProcess(cmd, 0, stdout="ffmpeg version", stderr="")


def test_install_existing_managed_pair_short_circuits(tmp_path):
    root = tmp_path / "managed"
    root.mkdir()
    (root / "ffmpeg.exe").write_bytes(b"x")
    (root / "ffprobe.exe").write_bytes(b"y")
    result = installer.install_ffmpeg(
        system="win32",
        machine="amd64",
        root=root,
        runner=_fake_runner,
        allow_system=False,
    )
    assert result.ok is True
    assert result.method == "existing"


def test_install_download_verifies_and_promotes(tmp_path):
    root = tmp_path / "managed"
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("build/bin/ffmpeg.exe", b"ffmpeg")
        archive.writestr("build/bin/ffprobe.exe", b"ffprobe")
    payload = archive_bytes.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    asset = "ffmpeg-n9.0-latest-win64-gpl-9.0.zip"

    def download(url, dest, **kwargs):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f"{digest}  {asset}\n", encoding="utf-8") if url.endswith(
            "checksums.sha256"
        ) else dest.write_bytes(payload)

    result = installer.install_ffmpeg(
        system="win32",
        machine="amd64",
        root=root,
        runner=_fake_runner,
        downloader=download,
    )
    assert result.ok is True
    assert result.method == "download"
    assert (root / "ffmpeg.exe").read_bytes() == b"ffmpeg"
    assert (root / "ffprobe.exe").read_bytes() == b"ffprobe"


def test_install_checksum_mismatch_leaves_no_binaries(tmp_path):
    root = tmp_path / "managed"
    payload = b"not-a-zip"
    asset = "ffmpeg-n9.0-latest-win64-gpl-9.0.zip"

    def download(url, dest, **kwargs):
        if url.endswith("checksums.sha256"):
            dest.write_text(f"{'0' * 64}  {asset}\n", encoding="utf-8")
        else:
            dest.write_bytes(payload)

    result = installer.install_ffmpeg(
        system="win32",
        machine="amd64",
        root=root,
        runner=_fake_runner,
        downloader=download,
    )
    assert result.ok is False
    assert "checksum" in result.message
    assert not (root / "ffmpeg.exe").exists()


def test_install_cancellation_and_manual_platform(tmp_path):
    cancel = threading.Event()
    cancel.set()
    result = installer.install_ffmpeg(system="win32", machine="amd64", root=tmp_path / "m", cancel_event=cancel)
    assert result.ok is False
    assert result.method == "cancelled"

    mac = installer.install_ffmpeg(system="darwin", machine="arm64", root=tmp_path / "m2")
    assert mac.ok is False
    assert "brew install ffmpeg" in mac.message


def test_install_system_mode_does_not_fall_back_to_download(tmp_path, monkeypatch):
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)
    result = installer.install_ffmpeg(
        mode="system",
        allow_system=True,
        system="linux",
        machine="x86_64",
        root=tmp_path / "m",
    )
    assert result.ok is False
    assert result.method == "system"
