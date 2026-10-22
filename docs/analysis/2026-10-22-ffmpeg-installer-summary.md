# FFmpeg Installer Delivery Summary

Date: 2026-10-22

## Status

R-028b and R-028c are implemented. Setup scripts and the Web UI share one verified installer;
the UI runs it as a cancellable Task Center job. Explicit `paths.ffmpeg` / `paths.ffprobe`
configuration is never modified.

## Compatibility Matrix

| Platform | Architecture | Automatic install | Fallback |
| --- | --- | --- | --- |
| Windows | x64 | BtbN `ffmpeg-n9.0-latest-win64-gpl-9.0.zip` | winget/choco/scoop, gyan.dev |
| Windows | arm64 | BtbN `ffmpeg-n9.0-latest-winarm64-gpl-9.0.zip` | winget, manual build |
| Linux | x86_64 | BtbN `ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz` | distribution package manager |
| Linux | aarch64 | BtbN `ffmpeg-n9.0-latest-linuxarm64-gpl-9.0.tar.xz` | distribution package manager |
| macOS | any | Not offered (no reliable checked static build) | Homebrew or manual build |

Unsupported architectures return a manual-install result instead of selecting an incompatible
asset. Linux armv7/i686 and musl-only distributions are not claimed as supported.

## Install Locations

- Windows: `%LOCALAPPDATA%\clio\ffmpeg`
- Linux/macOS: `~/.local/share/clio/ffmpeg`
- Override: `CLIO_FFMPEG_DIR`

`clio/utils.py::discover_ffmpeg_bin` checks the managed directory after `PATH`. Setup scripts
prepend the managed directory for their own verification commands; the desktop process resolves it
without requiring a terminal restart.

## Safety Contract

- Downloads require HTTPS and reject redirects to non-HTTPS URLs.
- The archive and checksum file come from the same fixed `n9.0` BtbN release.
- SHA256 mismatch, oversized responses, empty downloads, corrupt ZIP/TAR files, and unsupported
  platforms return a normal failed `InstallResult`.
- Archive entries reject path traversal, absolute paths, symlinks, hardlinks, devices, and
  duplicate names. Only `bin/ffmpeg` and `bin/ffprobe` are extracted.
- Both binaries must pass `-version` before an atomic promotion into the managed directory.
- Cancellation is checked during download and returns `cancelled`; the UI shows a retry action.

## UI Workflow

The missing-dependency warning opens `ffmpeg-install.js`:

1. Read `GET /api/deps/ffmpeg/install/status` for platform capability and an active task.
2. Start `POST /api/deps/ffmpeg/install`; the route submits `TaskKind.FFMPEG_INSTALL`.
3. Follow Task Center progress/cancel state and show platform-specific manual guidance.
4. On success, re-probe `/api/deps/ffmpeg`, refresh runtime warnings, and re-render media actions.

## Verification

- Installer security, cancellation, platform, and corrupt-archive tests: focused pytest suite green.
- Task route/status/duplicate-start/manager-missing tests: 4 passed.
- Frontend dialog helpers, progress, success refresh, and macOS fallback: Vitest green.
- `setup.ps1` PowerShell parser check passes; `setup.sh` macOS failure now falls through to the
  installer fallback because `set -e` is no longer allowed to abort at `brew install`.

## Follow-Up

- R-032e may bundle ffmpeg into a signed desktop release; do not remove the verified downloader
  until that path is available on all supported platforms.
- A future pass may add a mirror health check and optional proxy support for users whose network
  cannot reach GitHub. Mirrors must still provide same-release SHA256 checksums.
