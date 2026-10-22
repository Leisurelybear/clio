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


def _zip(
    path: Path,
    entries: dict[str, bytes],
    *,
    symlink: str | None = None,
    special: tuple[str, int] | None = None,
) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"target")
        if special:
            name, mode = special
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            archive.writestr(info, b"special")
    return path


def _tar(
    path: Path,
    entries: dict[str, bytes],
    *,
    symlink: str | None = None,
    mode: str = "w:gz",
    hardlink: tuple[str, str] | None = None,
    device: str | None = None,
    device_type: bytes = tarfile.CHRTYPE,
) -> Path:
    with tarfile.open(path, mode) as archive:
        for name, content in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        if symlink:
            info = tarfile.TarInfo(symlink)
            info.type = tarfile.SYMTYPE
            info.linkname = "/tmp/outside"
            archive.addfile(info)
        if hardlink:
            name, target = hardlink
            info = tarfile.TarInfo(name)
            info.type = tarfile.LNKTYPE
            info.linkname = target
            archive.addfile(info)
        if device:
            info = tarfile.TarInfo(device)
            info.type = device_type
            archive.addfile(info)
    return path


def test_select_asset_platform_matrix():
    assert installer.select_asset("win32", "AMD64").archive.endswith("win64-gpl-9.0.zip")
    assert installer.select_asset("win32", "arm64").archive.endswith("winarm64-gpl-9.0.zip")
    assert installer.select_asset("linux", "x86_64").archive.endswith("linux64-gpl-9.0.tar.xz")
    assert installer.select_asset("linux", "aarch64").archive.endswith("linuxarm64-gpl-9.0.tar.xz")
    assert installer.select_asset("darwin", "arm64") is None
    assert installer.select_asset("freebsd", "amd64") is None
    assert installer.select_asset("unsupported", "amd64") is None
    assert installer.select_asset("linux", "i686") is None


def test_platform_aliases_share_one_normalizer(tmp_path):
    assert installer.normalize_system("windows") == "win32"
    assert installer.normalize_system("linux") == "linux"
    assert installer.normalize_system("freebsd") == "unsupported"
    assert installer.managed_binary("ffmpeg", tmp_path, system="windows").name == "ffmpeg.exe"
    assert installer.select_asset("windows", "amd64") is not None


def test_managed_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CLIO_FFMPEG_DIR", str(tmp_path / "managed"))
    assert installer.managed_install_root() == (tmp_path / "managed").resolve()


def test_parse_checksum_exact_match_only():
    digest = "a" * 64
    assert installer.parse_checksum(f"{digest}  ffmpeg.zip\n", "ffmpeg.zip") == digest
    assert installer.parse_checksum(f"{digest} *ffmpeg.zip\n", "ffmpeg.zip") == digest
    with pytest.raises(ValueError, match="not found"):
        installer.parse_checksum(f"{digest}  other.zip\n", "ffmpeg.zip")


@pytest.mark.parametrize(
    "name",
    ["../evil", "/absolute", "a/../../evil", "", "C:/evil", "C:evil", r"\\server\share\evil"],
)
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


def test_extract_reads_botbn_tar_xz_asset_format(tmp_path):
    archive = _tar(
        tmp_path / "ffmpeg.tar.xz",
        {"ffmpeg-build/bin/ffmpeg": b"linux-ffmpeg", "ffmpeg-build/bin/ffprobe": b"linux-ffprobe"},
        mode="w:xz",
    )

    ffmpeg, ffprobe = installer.extract_binaries(archive, tmp_path / "out", exe_name="ffmpeg")

    assert ffmpeg.read_bytes() == b"linux-ffmpeg"
    assert ffprobe.read_bytes() == b"linux-ffprobe"


def test_extract_rejects_zip_symlink(tmp_path):
    archive = _zip(
        tmp_path / "ffmpeg.zip",
        {"ffmpeg-build/bin/ffmpeg.exe": b"x", "ffmpeg-build/bin/ffprobe.exe": b"y"},
        symlink="ffmpeg-build/bin/evil",
    )
    with pytest.raises(ValueError, match="unsafe archive entry type"):
        installer.extract_binaries(archive, tmp_path / "out", exe_name="ffmpeg.exe")


def test_extract_rejects_zip_special_files(tmp_path):
    archive = _zip(
        tmp_path / "ffmpeg.zip",
        {"ffmpeg-build/bin/ffmpeg.exe": b"x", "ffmpeg-build/bin/ffprobe.exe": b"y"},
        special=("ffmpeg-build/bin/evil-device", stat.S_IFCHR),
    )
    with pytest.raises(ValueError, match="unsafe archive entry type"):
        installer.extract_binaries(archive, tmp_path / "out", exe_name="ffmpeg.exe")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"hardlink": ("ffmpeg-build/bin/evil-hardlink", "ffmpeg-build/bin/ffmpeg")},
        {"device": "ffmpeg-build/bin/evil-device"},
        {"device": "ffmpeg-build/bin/evil-fifo", "device_type": tarfile.FIFOTYPE},
    ],
)
def test_extract_rejects_tar_hardlinks_and_devices(tmp_path, kwargs):
    archive = _tar(
        tmp_path / "ffmpeg.tar.gz",
        {"ffmpeg-build/bin/ffmpeg": b"x", "ffmpeg-build/bin/ffprobe": b"y"},
        **kwargs,
    )
    with pytest.raises(ValueError, match="unsafe archive entry type"):
        installer.extract_binaries(archive, tmp_path / "out", exe_name="ffmpeg")


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


def test_extract_rejects_declared_oversize_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "MAX_EXTRACTED_BINARY_BYTES", 4)
    archive = _zip(
        tmp_path / "ffmpeg.zip",
        {"build/bin/ffmpeg.exe": b"12345", "build/bin/ffprobe.exe": b"ok"},
    )

    with pytest.raises(ValueError, match="archive binary is too large"):
        installer.extract_binaries(archive, tmp_path / "out", exe_name="ffmpeg.exe")

    assert not (tmp_path / "out" / "ffmpeg.exe").exists()


def test_copy_stream_caps_actual_extracted_bytes(monkeypatch):
    monkeypatch.setattr(installer, "MAX_EXTRACTED_BINARY_BYTES", 4)
    with pytest.raises(ValueError, match="archive binary exceeded"):
        installer._copy_stream(io.BytesIO(b"12345"), io.BytesIO(), None)


def test_sha256_and_extract_observe_cancellation(tmp_path):
    archive = _zip(
        tmp_path / "ffmpeg.zip",
        {"build/bin/ffmpeg.exe": b"ffmpeg", "build/bin/ffprobe.exe": b"ffprobe"},
    )
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(InterruptedError):
        installer._sha256(archive, cancel)
    with pytest.raises(InterruptedError):
        installer.extract_binaries(archive, tmp_path / "out", exe_name="ffmpeg.exe", cancel_event=cancel)
    assert not (tmp_path / "out" / "ffmpeg.exe").exists()


def test_promote_rolls_back_both_binaries_when_second_replace_fails(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    root.mkdir()
    target_ffmpeg = root / "ffmpeg.exe"
    target_ffprobe = root / "ffprobe.exe"
    target_ffmpeg.write_bytes(b"old-ffmpeg")
    target_ffprobe.write_bytes(b"old-ffprobe")
    staged_dir = tmp_path / "staged"
    staged_dir.mkdir()
    staged_ffmpeg = staged_dir / "ffmpeg.exe"
    staged_ffprobe = staged_dir / "ffprobe.exe"
    staged_ffmpeg.write_bytes(b"new-ffmpeg")
    staged_ffprobe.write_bytes(b"new-ffprobe")

    real_replace = installer.os.replace

    def failing_replace(source, destination):
        if Path(destination) == target_ffprobe:
            raise OSError("simulated replace failure")
        return real_replace(source, destination)

    monkeypatch.setattr(installer.os, "replace", failing_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        installer._promote(staged_ffmpeg, staged_ffprobe, root)

    assert target_ffmpeg.read_bytes() == b"old-ffmpeg"
    assert target_ffprobe.read_bytes() == b"old-ffprobe"


def test_promote_rolls_back_when_final_verification_fails(tmp_path):
    root = tmp_path / "managed"
    root.mkdir()
    target_ffmpeg = root / "ffmpeg.exe"
    target_ffprobe = root / "ffprobe.exe"
    target_ffmpeg.write_bytes(b"old-ffmpeg")
    target_ffprobe.write_bytes(b"old-ffprobe")
    staged_dir = tmp_path / "staged"
    staged_dir.mkdir()
    staged_ffmpeg = staged_dir / "ffmpeg.exe"
    staged_ffprobe = staged_dir / "ffprobe.exe"
    staged_ffmpeg.write_bytes(b"new-ffmpeg")
    staged_ffprobe.write_bytes(b"new-ffprobe")

    with pytest.raises(ValueError, match="failed verification"):
        installer._promote(staged_ffmpeg, staged_ffprobe, root, verify=lambda _ffmpeg, _ffprobe: False)

    assert target_ffmpeg.read_bytes() == b"old-ffmpeg"
    assert target_ffprobe.read_bytes() == b"old-ffprobe"


def test_promote_cancellation_before_replace_keeps_old_binaries(tmp_path):
    root = tmp_path / "managed"
    root.mkdir()
    target_ffmpeg = root / "ffmpeg.exe"
    target_ffprobe = root / "ffprobe.exe"
    target_ffmpeg.write_bytes(b"old-ffmpeg")
    target_ffprobe.write_bytes(b"old-ffprobe")
    staged_dir = tmp_path / "staged"
    staged_dir.mkdir()
    staged_ffmpeg = staged_dir / "ffmpeg.exe"
    staged_ffprobe = staged_dir / "ffprobe.exe"
    staged_ffmpeg.write_bytes(b"new-ffmpeg")
    staged_ffprobe.write_bytes(b"new-ffprobe")
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(InterruptedError):
        installer._promote(staged_ffmpeg, staged_ffprobe, root, cancel_event=cancel)

    assert target_ffmpeg.read_bytes() == b"old-ffmpeg"
    assert target_ffprobe.read_bytes() == b"old-ffprobe"


def test_promote_cancellation_between_replacements_rolls_back_first(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    root.mkdir()
    target_ffmpeg = root / "ffmpeg.exe"
    target_ffprobe = root / "ffprobe.exe"
    target_ffmpeg.write_bytes(b"old-ffmpeg")
    target_ffprobe.write_bytes(b"old-ffprobe")
    staged_dir = tmp_path / "staged"
    staged_dir.mkdir()
    staged_ffmpeg = staged_dir / "ffmpeg.exe"
    staged_ffprobe = staged_dir / "ffprobe.exe"
    staged_ffmpeg.write_bytes(b"new-ffmpeg")
    staged_ffprobe.write_bytes(b"new-ffprobe")
    cancel = threading.Event()
    real_replace = installer.os.replace

    def replace_then_cancel(source, destination):
        real_replace(source, destination)
        if Path(destination) == target_ffmpeg:
            cancel.set()

    monkeypatch.setattr(installer.os, "replace", replace_then_cancel)

    with pytest.raises(InterruptedError):
        installer._promote(staged_ffmpeg, staged_ffprobe, root, cancel_event=cancel)

    assert target_ffmpeg.read_bytes() == b"old-ffmpeg"
    assert target_ffprobe.read_bytes() == b"old-ffprobe"


def test_download_requires_https():
    with pytest.raises(ValueError, match="HTTPS"):
        installer._download(
            "http://example.test/ffmpeg.zip", Path("unused"), max_bytes=1, reporter=None, cancel_event=None
        )


def test_download_observes_cancellation_before_network(monkeypatch, tmp_path):
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(
        installer.urllib.request,
        "urlopen",
        lambda *args, **kwargs: pytest.fail("network must not be used after cancellation"),
    )
    dest = tmp_path / "file.bin"

    with pytest.raises(InterruptedError):
        installer._download("https://example.test/a", dest, max_bytes=100, reporter=None, cancel_event=cancel)

    assert not dest.exists()


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


def test_download_accepts_https_redirect(monkeypatch, tmp_path):
    monkeypatch.setattr(
        installer.urllib.request,
        "urlopen",
        lambda request, timeout=30: _Response(b"abc", url="https://release-assets.example/file"),
    )
    dest = tmp_path / "file.bin"

    installer._download("https://example.test/a", dest, max_bytes=100, reporter=None, cancel_event=None)

    assert dest.read_bytes() == b"abc"


def test_download_removes_partial_file_on_failure(monkeypatch, tmp_path):
    class FailingResponse(_Response):
        def __init__(self):
            super().__init__(b"")
            self.calls = 0

        def read(self, size):
            self.calls += 1
            if self.calls == 1:
                return b"partial"
            raise OSError("connection dropped")

    monkeypatch.setattr(installer.urllib.request, "urlopen", lambda request, timeout=30: FailingResponse())
    dest = tmp_path / "file.bin"

    with pytest.raises(OSError, match="connection dropped"):
        installer._download("https://example.test/a", dest, max_bytes=100, reporter=None, cancel_event=None)

    assert not dest.exists()


def test_download_rejects_truncated_content_length(monkeypatch, tmp_path):
    monkeypatch.setattr(
        installer.urllib.request,
        "urlopen",
        lambda request, timeout=30: _Response(b"abc", length=10),
    )
    dest = tmp_path / "file.bin"

    with pytest.raises(ValueError, match="incomplete"):
        installer._download("https://example.test/a", dest, max_bytes=100, reporter=None, cancel_event=None)

    assert not dest.exists()


def test_download_caps_stream_without_content_length(monkeypatch, tmp_path):
    class NoLengthResponse(_Response):
        def __init__(self):
            super().__init__(b"12345")
            self.headers = {}

    monkeypatch.setattr(
        installer.urllib.request,
        "urlopen",
        lambda request, timeout=30: NoLengthResponse(),
    )
    dest = tmp_path / "file.bin"

    with pytest.raises(ValueError, match="exceeded"):
        installer._download("https://example.test/a", dest, max_bytes=4, reporter=None, cancel_event=None)

    assert not dest.exists()


def test_download_removes_empty_failed_file(monkeypatch, tmp_path):
    monkeypatch.setattr(installer.urllib.request, "urlopen", lambda request, timeout=30: _Response(b""))
    dest = tmp_path / "file.bin"

    with pytest.raises(ValueError, match="empty"):
        installer._download("https://example.test/a", dest, max_bytes=100, reporter=None, cancel_event=None)

    assert not dest.exists()


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


def test_install_corrupt_archive_returns_failure(tmp_path):
    root = tmp_path / "managed"
    payload = b"not-an-archive"
    asset = "ffmpeg-n9.0-latest-win64-gpl-9.0.zip"

    def download(url, dest, **kwargs):
        if url.endswith("checksums.sha256"):
            digest = hashlib.sha256(payload).hexdigest()
            dest.write_text(f"{digest}  {asset}\n", encoding="utf-8")
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
    assert result.message
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


def test_cli_reports_install_result(monkeypatch, capsys):
    monkeypatch.setattr(
        installer,
        "install_ffmpeg",
        lambda **kwargs: installer.InstallResult(True, "download", "/tmp/ffmpeg", "/tmp/ffprobe", "installed"),
    )
    assert installer.main(["--mode", "download"]) == 0
    assert "installed" in capsys.readouterr().out
