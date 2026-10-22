"""Verified, cross-platform FFmpeg installer used by setup scripts and the UI."""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import urllib.request
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from clio.utils import run_subprocess

RELEASE_BASE = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"
CHECKSUMS_URL = f"{RELEASE_BASE}/checksums.sha256"
MAX_ARCHIVE_BYTES = 350 * 1024 * 1024
MAX_CHECKSUM_BYTES = 1024 * 1024
MAX_EXTRACTED_BINARY_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True)
class InstallResult:
    ok: bool
    method: str
    ffmpeg: str = ""
    ffprobe: str = ""
    message: str = ""


@dataclass(frozen=True)
class _Asset:
    name: str
    archive: str


_ASSETS: dict[tuple[str, str], _Asset] = {
    ("win32", "amd64"): _Asset("ffmpeg.exe", "ffmpeg-n9.0-latest-win64-gpl-9.0.zip"),
    ("win32", "arm64"): _Asset("ffmpeg.exe", "ffmpeg-n9.0-latest-winarm64-gpl-9.0.zip"),
    ("linux", "x86_64"): _Asset("ffmpeg", "ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz"),
    ("linux", "aarch64"): _Asset("ffmpeg", "ffmpeg-n9.0-latest-linuxarm64-gpl-9.0.tar.xz"),
}

_PROGRESS = Callable[[int, str], None]


def _emit(reporter: _PROGRESS | None, pct: int, message: str) -> None:
    if reporter is not None:
        try:
            reporter(max(0, min(100, int(pct))), message)
        except Exception:  # noqa: BLE001 - progress must not break installation
            pass


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("ffmpeg installation cancelled")


def managed_install_root() -> Path:
    override = os.environ.get("CLIO_FFMPEG_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / "clio" / "ffmpeg"
    return Path.home() / ".local" / "share" / "clio" / "ffmpeg"


def normalize_system(value: str | None = None) -> str:
    """Normalize platform aliases; unsupported systems never become Linux."""
    raw = str(value or "").strip().lower()
    if not raw:
        detected = platform.system().strip().lower()
        if detected == "windows":
            return "win32"
        if detected == "darwin":
            return "darwin"
        if detected == "linux":
            return "linux"
        return "unsupported" if detected else ("win32" if os.name == "nt" else "unsupported")
    if raw in {"win32", "nt", "windows"}:
        return "win32"
    if raw in {"darwin", "mac", "macos", "osx"}:
        return "darwin"
    if raw == "linux" or raw.startswith("linux"):
        return "linux"
    return "unsupported"


def managed_binary(name: str, root: Path | None = None, *, system: str | None = None) -> Path:
    is_windows = normalize_system(system) == "win32"
    suffix = ".exe" if is_windows and not name.lower().endswith(".exe") else ""
    return (root or managed_install_root()) / f"{name}{suffix}"


def _normalized_machine(value: str | None = None, *, windows: bool = False) -> str:
    raw = (value or platform.machine() or "").strip().lower()
    if raw in {"amd64", "x86_64", "x64"}:
        return "amd64" if windows else "x86_64"
    if raw in {"arm64", "aarch64"}:
        return "arm64" if windows else "aarch64"
    return raw


def select_asset(system: str | None = None, machine: str | None = None) -> _Asset | None:
    system_key = normalize_system(system)
    if system_key not in {"win32", "linux"}:
        return None
    machine_key = _normalized_machine(machine, windows=system_key == "win32")
    return _ASSETS.get((system_key, machine_key))


def parse_checksum(text: str, asset_name: str) -> str:
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line.strip())
        if match and match.group(2).strip() == asset_name:
            return match.group(1).lower()
    raise ValueError(f"checksum not found for {asset_name}")


def _content_length(response: object) -> int | None:
    headers = getattr(response, "headers", None)
    raw = headers.get("Content-Length") if headers is not None else None
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _download(
    url: str, dest: Path, *, max_bytes: int, reporter: _PROGRESS | None, cancel_event: threading.Event | None
) -> None:
    if not url.lower().startswith("https://"):
        raise ValueError("ffmpeg downloads require HTTPS")
    _check_cancel(cancel_event)
    request = urllib.request.Request(url, headers={"User-Agent": "clio-ffmpeg-installer"})
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            final_url = response.geturl()
            if not str(final_url).lower().startswith("https://"):
                raise ValueError("ffmpeg download redirected to a non-HTTPS URL")
            expected = _content_length(response)
            if expected is not None and expected > max_bytes:
                raise ValueError(f"ffmpeg download is too large: {expected} bytes")
            with dest.open("wb") as out:
                while True:
                    _check_cancel(cancel_event)
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"ffmpeg download exceeded {max_bytes} bytes")
                    out.write(chunk)
                    if expected:
                        _emit(reporter, 15 + int(total / expected * 60), f"Downloading ({total // 1048576} MB)")
                if expected is not None and total != expected:
                    raise ValueError(f"ffmpeg download incomplete: expected {expected} bytes, got {total}")
                if total == 0:
                    raise ValueError("ffmpeg download was empty")
    except BaseException:
        dest.unlink(missing_ok=True)
        raise


def _safe_member_name(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or windows_path.is_absolute()
        or re.match(r"^[A-Za-z]:", normalized)
        or ".." in path.parts
    ):
        raise ValueError(f"unsafe archive path: {name!r}")
    return path


def _validate_entries(entries: list[tuple[str, str | None]]) -> None:
    seen: set[str] = set()
    for name, kind in entries:
        path = _safe_member_name(name)
        key = str(path).lower() if os.name == "nt" else str(path)
        if key in seen:
            raise ValueError(f"duplicate archive entry: {name!r}")
        seen.add(key)
        if kind is not None:
            raise ValueError(f"unsafe archive entry type: {name!r} ({kind})")


def _find_binaries(names: list[str], exe_name: str) -> tuple[str, str]:
    wanted = {exe_name.lower(), "ffprobe" + (".exe" if exe_name.lower().endswith(".exe") else "").lower()}
    found: dict[str, list[str]] = {name: [] for name in wanted}
    for name in names:
        path = _safe_member_name(name)
        if len(path.parts) < 2 or path.parts[-2].lower() != "bin":
            continue
        base = path.parts[-1].lower()
        if base in found:
            found[base].append(name)
    for base, matches in found.items():
        if len(matches) != 1:
            raise ValueError(f"archive must contain exactly one bin/{base}")
    return found[exe_name.lower()][0], found["ffprobe" + (".exe" if exe_name.lower().endswith(".exe") else "").lower()][
        0
    ]


def _zip_entries(archive: zipfile.ZipFile) -> list[tuple[str, str | None]]:
    entries: list[tuple[str, str | None]] = []
    for info in archive.infolist():
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            kind = "symlink"
        elif mode not in {0, stat.S_IFREG, stat.S_IFDIR}:
            kind = f"special ({oct(mode)})"
        else:
            kind = None
        entries.append((info.filename, kind))
    return entries


def _tar_entries(archive: tarfile.TarFile) -> list[tuple[str, str | None]]:
    entries: list[tuple[str, str | None]] = []
    for member in archive.getmembers():
        if member.isfile() or member.isdir():
            kind = None
        elif member.issym():
            kind = "symlink"
        elif member.islnk():
            kind = "hardlink"
        elif member.ischr():
            kind = "character device"
        elif member.isblk():
            kind = "block device"
        elif member.isfifo():
            kind = "fifo"
        else:
            kind = f"special type {member.type!r}"
        entries.append((member.name, kind))
    return entries


def _check_extracted_size(name: str, size: int) -> None:
    if size < 0 or size > MAX_EXTRACTED_BINARY_BYTES:
        raise ValueError(f"archive binary is too large: {name!r} ({size} bytes)")


def _copy_stream(src, out, cancel_event: threading.Event | None) -> None:
    written = 0
    while True:
        _check_cancel(cancel_event)
        chunk = src.read(1024 * 1024)
        if not chunk:
            return
        written += len(chunk)
        if written > MAX_EXTRACTED_BINARY_BYTES:
            raise ValueError(f"archive binary exceeded {MAX_EXTRACTED_BINARY_BYTES} bytes")
        out.write(chunk)


def extract_binaries(
    archive_path: Path,
    dest_dir: Path,
    *,
    exe_name: str | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[Path, Path]:
    exe_name = exe_name or ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    dest_dir.mkdir(parents=True, exist_ok=True)
    _check_cancel(cancel_event)
    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as archive:
            entries = _zip_entries(archive)
            _validate_entries(entries)
            ffmpeg_name, ffprobe_name = _find_binaries([name for name, _ in entries], exe_name)
            for source_name, target in ((ffmpeg_name, exe_name), (ffprobe_name, "ffprobe" + Path(exe_name).suffix)):
                _check_cancel(cancel_event)
                _check_extracted_size(source_name, archive.getinfo(source_name).file_size)
                with archive.open(source_name) as src, (dest_dir / target).open("wb") as out:
                    _copy_stream(src, out, cancel_event)
    else:
        with tarfile.open(archive_path, "r:*") as archive:
            entries = _tar_entries(archive)
            _validate_entries(entries)
            ffmpeg_name, ffprobe_name = _find_binaries([name for name, _ in entries], exe_name)
            for source_name, target in ((ffmpeg_name, exe_name), (ffprobe_name, "ffprobe" + Path(exe_name).suffix)):
                _check_cancel(cancel_event)
                member = archive.getmember(source_name)
                _check_extracted_size(source_name, member.size)
                if not member.isfile():
                    raise ValueError(f"archive binary is not a regular file: {source_name!r}")
                src = archive.extractfile(member)
                if src is None:
                    raise ValueError(f"cannot read archive binary: {source_name!r}")
                with src, (dest_dir / target).open("wb") as out:
                    _copy_stream(src, out, cancel_event)
    ffmpeg_path = dest_dir / exe_name
    ffprobe_path = dest_dir / ("ffprobe" + Path(exe_name).suffix)
    for path in (ffmpeg_path, ffprobe_path):
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"archive did not yield {path.name}")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return ffmpeg_path, ffprobe_path


def verify_binaries(ffmpeg: Path, ffprobe: Path, *, runner: Callable[..., object] | None = None) -> bool:
    run = runner or run_subprocess
    for binary in (ffmpeg, ffprobe):
        if not binary.is_file():
            return False
        try:
            result = run(
                [str(binary), "-version"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except Exception:  # noqa: BLE001
            return False
        if getattr(result, "returncode", 1) != 0:
            return False
    return True


def _sha256(path: Path, cancel_event: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            _check_cancel(cancel_event)
            digest.update(chunk)
    return digest.hexdigest()


def _package_manager_commands(system: str) -> list[list[str]]:
    system = normalize_system(system)
    if system == "win32":
        return [
            [
                "winget",
                "install",
                "--id",
                "Gyan.FFmpeg",
                "-e",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ],
            ["choco", "install", "ffmpeg", "-y"],
            ["scoop", "install", "ffmpeg"],
        ]
    if system == "darwin":
        return [["brew", "install", "ffmpeg"]]
    return []


def _try_package_manager(system: str, reporter: _PROGRESS | None, runner: Callable[..., object]) -> None:
    for command in _package_manager_commands(system):
        if not shutil.which(command[0]):
            continue
        _emit(reporter, 8, f"Trying {command[0]}")
        try:
            result = runner(command, capture_output=True, text=True, timeout=900, check=False)
        except Exception:  # noqa: BLE001
            continue
        if getattr(result, "returncode", 1) == 0:
            return


def _promote(
    staged_ffmpeg: Path,
    staged_ffprobe: Path,
    root: Path,
    *,
    cancel_event: threading.Event | None = None,
    verify: Callable[[Path, Path], bool] | None = None,
) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    target_ffmpeg = root / staged_ffmpeg.name
    target_ffprobe = root / staged_ffprobe.name
    token = uuid.uuid4().hex
    temp_ffmpeg = root / f".{target_ffmpeg.name}.{token}.tmp"
    temp_ffprobe = root / f".{target_ffprobe.name}.{token}.tmp"
    backups = {
        target_ffmpeg: root / f".{target_ffmpeg.name}.{token}.bak",
        target_ffprobe: root / f".{target_ffprobe.name}.{token}.bak",
    }
    replaced: set[Path] = set()
    preserve_backups = False
    try:
        _check_cancel(cancel_event)
        shutil.copy2(staged_ffmpeg, temp_ffmpeg)
        shutil.copy2(staged_ffprobe, temp_ffprobe)
        _check_cancel(cancel_event)
        for target, backup in backups.items():
            if target.exists():
                shutil.copy2(target, backup)
        _check_cancel(cancel_event)
        os.replace(temp_ffmpeg, target_ffmpeg)
        replaced.add(target_ffmpeg)
        _check_cancel(cancel_event)
        os.replace(temp_ffprobe, target_ffprobe)
        replaced.add(target_ffprobe)
        if verify is not None and not verify(target_ffmpeg, target_ffprobe):
            raise ValueError("installed FFmpeg binaries failed verification")
        _check_cancel(cancel_event)
    except BaseException:
        for target in replaced:
            backup = backups[target]
            try:
                if backup.exists():
                    os.replace(backup, target)
                else:
                    target.unlink(missing_ok=True)
            except OSError:
                preserve_backups = True
        raise
    finally:
        temp_ffmpeg.unlink(missing_ok=True)
        temp_ffprobe.unlink(missing_ok=True)
        if not preserve_backups:
            for backup in backups.values():
                backup.unlink(missing_ok=True)
    return target_ffmpeg, target_ffprobe


def install_ffmpeg(
    *,
    mode: str = "auto",
    allow_system: bool = False,
    reporter: _PROGRESS | None = None,
    cancel_event: threading.Event | None = None,
    system: str | None = None,
    machine: str | None = None,
    root: Path | None = None,
    runner: Callable[..., object] | None = None,
    downloader: Callable[..., None] | None = None,
) -> InstallResult:
    """Install FFmpeg into the managed user directory; never raises for expected failures."""
    system_name = normalize_system(system)
    run = runner or run_subprocess
    asset = select_asset(system_name, machine)
    install_root = root or managed_install_root()
    try:
        _check_cancel(cancel_event)
        _emit(reporter, 3, "Checking existing binaries")
        existing_ffmpeg = managed_binary("ffmpeg", install_root, system=system_name)
        existing_ffprobe = managed_binary("ffprobe", install_root, system=system_name)
        if verify_binaries(existing_ffmpeg, existing_ffprobe, runner=run):
            return InstallResult(
                True, "existing", str(existing_ffmpeg), str(existing_ffprobe), "ffmpeg already installed"
            )

        if allow_system and mode in {"auto", "system"}:
            _try_package_manager(system_name, reporter, run)
            found_ffmpeg = shutil.which("ffmpeg")
            found_ffprobe = shutil.which("ffprobe")
            if found_ffmpeg and found_ffprobe and verify_binaries(Path(found_ffmpeg), Path(found_ffprobe), runner=run):
                return InstallResult(True, "system", found_ffmpeg, found_ffprobe, "ffmpeg installed by package manager")

        if mode == "system":
            return InstallResult(False, "system", message="package manager did not provide ffmpeg/ffprobe")
        if asset is None:
            if system_name == "darwin":
                return InstallResult(False, "manual", message="Install Homebrew and run: brew install ffmpeg")
            if system_name == "unsupported":
                return InstallResult(
                    False,
                    "manual",
                    message="Automatic FFmpeg installation is not supported on this platform; install ffmpeg manually",
                )
            return InstallResult(
                False, "manual", message=f"No verified FFmpeg build for {system_name} {machine or platform.machine()}"
            )

        _emit(reporter, 10, "Preparing verified download")
        with tempfile.TemporaryDirectory(prefix="clio-ffmpeg-") as temp_name:
            temp = Path(temp_name)
            checksum_file = temp / "checksums.sha256"
            archive_file = temp / asset.archive
            download = downloader or _download
            download(
                CHECKSUMS_URL,
                checksum_file,
                max_bytes=MAX_CHECKSUM_BYTES,
                reporter=None,
                cancel_event=cancel_event,
            )
            _check_cancel(cancel_event)
            expected = parse_checksum(checksum_file.read_text(encoding="utf-8"), asset.archive)
            _emit(reporter, 15, "Downloading FFmpeg")
            download(
                f"{RELEASE_BASE}/{asset.archive}",
                archive_file,
                max_bytes=MAX_ARCHIVE_BYTES,
                reporter=reporter,
                cancel_event=cancel_event,
            )
            _check_cancel(cancel_event)
            actual = _sha256(archive_file, cancel_event)
            if actual != expected:
                return InstallResult(False, "download", message="FFmpeg archive checksum mismatch")
            _emit(reporter, 82, "Extracting binaries")
            staged_dir = temp / "staged"
            staged_ffmpeg, staged_ffprobe = extract_binaries(
                archive_file, staged_dir, exe_name=asset.name, cancel_event=cancel_event
            )
            if not verify_binaries(staged_ffmpeg, staged_ffprobe, runner=run):
                return InstallResult(False, "download", message="downloaded FFmpeg binaries failed verification")
            _check_cancel(cancel_event)
            _emit(reporter, 94, "Installing")
            final_ffmpeg, final_ffprobe = _promote(
                staged_ffmpeg,
                staged_ffprobe,
                install_root,
                cancel_event=cancel_event,
                verify=lambda ffmpeg, ffprobe: verify_binaries(ffmpeg, ffprobe, runner=run),
            )
            _emit(reporter, 100, "FFmpeg installed")
            return InstallResult(True, "download", str(final_ffmpeg), str(final_ffprobe), "ffmpeg installed")
    except InterruptedError:
        return InstallResult(False, "cancelled", message="ffmpeg installation cancelled")
    except (OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile, tarfile.TarError) as exc:
        return InstallResult(False, "download", message=str(exc))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install FFmpeg into the Clio managed directory")
    parser.add_argument("--mode", choices=("auto", "system", "download"), default="auto")
    parser.add_argument("--allow-system", action="store_true", help="Try user-accessible package managers first")
    args = parser.parse_args(argv)
    result = install_ffmpeg(mode=args.mode, allow_system=args.allow_system)
    print(result.message)
    if result.ok:
        print(result.ffmpeg)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
