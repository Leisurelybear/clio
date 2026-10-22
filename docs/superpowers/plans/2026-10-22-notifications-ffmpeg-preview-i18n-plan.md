# Notifications, FFmpeg Installer, Preview Timeline, and I18n Plan

Status: In progress (2026-10-22)

## Objective

Close four remaining product gaps without destabilizing the existing pipeline:

1. R-042: opt-in OS notifications while the desktop/Web UI is backgrounded.
2. FFmpeg: cross-platform automatic installation with safe download and archive handling.
3. R-031b: plan preview should prefer rendered cut clips and fall back to source-video seeking.
4. R-025: a small, extensible internationalization foundation with Chinese and English UI catalogs.

The persistent Notification Center remains the source of truth. Automatic installation is
best-effort and never overrides explicit user paths. Preview compatibility is preserved by a
strict fallback chain. I18n starts with shared shell and high-signal messages, not a risky
one-shot translation of every string.

## Baseline

- Python: 2182 passed, 12 skipped; coverage 81.67%.
- Frontend: 562 Vitest tests across 54 files.
- Recent CI run 39722308010 passed.
- Existing R-042 inbox, SSE stream, unread count, task deep links, and duplicate suppression work.
- R-031a/a2 already provide a global scrubber, composite clock, cross-clip switching, and waveform.
- `setup.ps1` and `setup.sh` install ffmpeg through package managers but have no verified static fallback.
- No locale catalog or runtime resolver exists yet.

## Non-Goals

- Do not replace the persistent Notification Center with native notifications.
- Do not request elevation/sudo from the Web UI.
- Do not silently change `paths.ffmpeg` or `paths.ffprobe` when either is explicitly configured.
- Do not translate AI prompts in this phase; prompt language remains controlled by trip/context data.
- Do not physically re-render or concatenate a day export for preview.
- Do not guess custom `cut-outdir` locations or accept arbitrary media paths from the frontend.
- Do not support unsigned third-party binary mirrors.

## Shared Configuration Decision

Add one global-only `ui` section:

```yaml
ui:
  language: zh-CN
  desktop_notifications: false
```

`language` accepts `zh-CN` and `en`. Unknown values fail config validation rather than silently
falling back. `desktop_notifications` is opt-in and defaults to false.

The section is global-only because language and shell behavior apply to the whole installation.
It is introduced before R-042 and reused by R-025. Both example configs, schema labels, runtime
validation, and migration tests must change together.

## R-042: Desktop OS Notifications

### Behavior

- Register a notification in the existing inbox exactly as today.
- If `ui.desktop_notifications` is true and the page is hidden, deliver a best-effort OS notification.
- If the page is visible, do not deliver a duplicate native notification.
- In pywebview, use a small backend helper exposed through the desktop API.
- In browser mode, use the browser Notification API only after an explicit user gesture/permission.
- Partial success is acceptable: inbox persistence must never depend on native delivery.
- Delivery failures are silent to the task and recorded only in debug logs.

### Backend

Create `clio/desktop/notifications.py` with a narrow API:

```python
send_desktop_notification(title: str, message: str, *, timeout_sec: float = 10.0) -> bool
```

Platform mapping:

| Platform | Mechanism | Failure behavior |
|---|---|---|
| Windows | PowerShell toast/notification command with safe argument passing | return false |
| macOS | `osascript` with escaped strings | return false |
| Linux | `notify-send` when available | return false |

Rules:

- Never use a shell string built from untrusted notification text.
- Clamp title/message length and strip control characters.
- Absolute timeout and `no_console_kwargs()` on Windows.
- No new Python dependency.
- Expose `DesktopApi.notify(title, message)` returning `{ok, delivered, reason?}`.

### Frontend

- Add a pure helper module, `desktop-notify.js`, that decides delivery eligibility and maps payloads.
- Hook the existing `clio:notification-created` event after inbox upsert.
- Add a setting row for the opt-in switch.
- Browser permission is requested only from an explicit settings action, never from a background event.
- Native notifications use the task/notification deep link only inside the app; OS bodies stay plain text.

### Tests

- Helper command construction for Windows/macOS/Linux, escaping, timeout, and missing executable.
- Desktop API envelope and exception swallowing.
- Frontend eligibility: visible/hidden, disabled, unsupported, duplicate event, and permission denied.
- No notification-center regression: registration, SSE merge, unread count, and dedupe.

## FFmpeg Installer

### Verified Sources and Compatibility

Verified on 2026-10-22 from BtbN `FFmpeg-Builds` release `latest`:

- `ffmpeg-n9.0-latest-win64-gpl-9.0.zip`
- `ffmpeg-n9.0-latest-winarm64-gpl-9.0.zip`
- `ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz`
- `ffmpeg-n9.0-latest-linuxarm64-gpl-9.0.tar.xz`
- `checksums.sha256` is an asset of the same release.

Because the build tag is rolling, the installer downloads `checksums.sha256` from the same release
at install time, selects the exact asset, and verifies its SHA256 before extraction. HTTPS is
mandatory; redirects to non-HTTPS are rejected.

Platform matrix:

| Platform | Primary | Fallback | Notes |
|---|---|---|---|
| Windows x64/arm64 | winget/choco/scoop | BtbN archive under `%LOCALAPPDATA%/clio/ffmpeg` | No elevation from Web UI |
| Linux x64/arm64 | apt/dnf/pacman/zypper | BtbN archive under `~/.local/share/clio/ffmpeg` | Package manager commands may require sudo in setup scripts only |
| macOS x64/arm64 | Homebrew | actionable manual message | No verified static artifact is pinned in this phase |

### Core Module

Create `clio/ffmpeg_install.py` as the only installer implementation:

- `install_ffmpeg(mode="auto", reporter=None, cancel_event=None) -> InstallResult`
- Managed root resolution and PATH-free binary lookup.
- Package-manager attempt and post-install probe.
- Static archive download with progress, size cap, content length, HTTPS redirect checks, and SHA256.
- Safe ZIP/TAR extraction: reject absolute paths, `..`, symlinks, devices, and writes outside staging.
- Atomic promotion into the managed root; never overwrite a valid installation with a partial one.
- Probe both binaries with `-version` before success.
- Preserve explicit config paths; only write `paths.ffmpeg/ffprobe` when both are empty and configurable.

### Integration

- Extend `discover_ffmpeg_bin()` to include the managed root.
- Add `TaskKind.FFMPEG_INSTALL` and a background executor with progress/error reporting.
- Add `POST /api/deps/ffmpeg/install` and `GET /api/deps/ffmpeg/install/status`.
- Replace the warning action `show-ffmpeg-help` with an install dialog offering automatic install and
  platform-specific manual steps.
- Update `setup.ps1`/`setup.sh` to call the same Python core after package-manager failure.

### Security and Tests

- Reject HTTP URLs, oversized archives, checksum mismatches, archive traversal, symlinks, and duplicate
  entries.
- Unit-test architecture mapping, checksum parsing, redirect policy, safe extraction, cancellation,
  partial cleanup, config write policy, and probe failure.
- Route tests cover auth, concurrent install rejection, missing platform, progress status, and task
  terminal notifications.
- Do not execute a real download in CI; inject a downloader/runner fixture.

## R-031b: Cut-Clip Preview Timeline

### Resolution Order

For each plan sequence segment:

1. A verified cut clip from standard `output/cuts/<day>/`.
2. Existing R-031a source-video seek hop.

Day composite export is not included until a stable, documented artifact contract exists.

### Backend Mapping

- Read the plan and match each sequence index to `{video_index}_{title}_seg_{i:03d}.mp4`.
- Accept only direct children of the resolved standard cuts directory.
- Validate `_cut_info` when present and reject mismatched segment index/timeline range.
- Return metadata only: segment index, relative media token/URL, clip duration, and source fallback info.
- Never accept an arbitrary filesystem path from the client.
- Custom `cut-outdir` remains unsupported for mapping unless it can be proven to equal the standard root.

### Frontend

- Add a pure `plan-preview-media.js` mapping layer.
- Prefer cut-clip URL and local time within the cut; preserve source offsets for fallback.
- Update viewer segment switching and preload cache keys to use media identity plus timing.
- Keep the composite clock and waveform untouched.
- If a cut file disappears between mapping and playback, retry once via source fallback.

### Tests

- Python: standard path mapping, ordering, duplicates, missing info, `_cut_info` mismatch, traversal,
  symlink escape, custom output, and partial cuts.
- Vitest: segment media selection, local-vs-source seek, preload identity, stale cut retry, and no-cut
  regression for R-031a.

## R-025: Internationalization

### Catalog and Resolver

- Add `clio/locales/zh-CN.json` and `clio/locales/en.json`.
- Add frontend `i18n.js` with `t(key, params)` and deterministic fallback `locale -> en -> zh-CN -> key`.
- Add `clio/i18n.py` for CLI/backend messages using the same JSON files.
- Cache catalogs per process and expose a test-only cache reset.
- Missing keys never crash; they return the key and report once in debug logging.

### Runtime

- Persist `ui.language` in global config.
- Expose the effective language in config responses and desktop state where needed.
- Set `<html lang>` after load and on change.
- Add a settings dropdown (`中文`, `English`) that applies immediately and persists.
- Keep stored notification text immutable; translation applies to newly generated UI copy only.

### Scope for This Pass

- Shared navigation/header, settings shell, notification center labels, runtime warnings, ffmpeg install
  dialog, common empty/error states, and doctor/CLI dependency messages.
- Do not translate user/AI-authored content, plan titles, transcripts, logs already on disk, or prompts.
- Any remaining hard-coded strings are explicitly listed as follow-up work rather than silently claimed complete.

### Packaging

- Add `clio/locales/*.json` to setuptools package data and PyInstaller resource collection if present.
- Add a wheel/resource test proving both catalogs ship.

## Commit and Push Sequence

Each row is a separate commit and push. A row is not started until the preceding focused tests pass.

| # | Commit type | Scope |
|---|---|---|
| 1 | docs | This plan |
| 2 | feat(config) | Global `ui` section, validation, schema, examples, tests |
| 3 | feat(desktop) | Cross-platform native notification helper and desktop API |
| 4 | feat(ui) | R-042 opt-in, hidden-page delivery, browser permission flow, tests |
| 5 | docs | R-042 completion notes and roadmap |
| 6 | feat(ffmpeg) | Installer core, verified download, safe extraction, tests |
| 7 | feat(setup) | setup scripts reuse installer fallback |
| 8 | feat(ui) | FFmpeg install task, API, warning dialog, progress, tests |
| 9 | docs | FFmpeg compatibility/install documentation and roadmap |
| 10 | feat(ui) | R-031b preview media mapping API and security tests |
| 11 | feat(ui) | Cut-clip-first viewer/preload with source fallback and Vitest |
| 12 | docs | R-031b completion notes and roadmap |
| 13 | feat(i18n) | Catalogs, Python/JS resolvers, packaged resources, tests |
| 14 | feat(ui) | Migrate shared shell, notifications, warnings, settings copy |
| 15 | feat(cli) | Migrate doctor/dependency/CLI messages and tests |
| 16 | docs | R-025 phase status, remaining-string inventory, roadmap |

## Verification

Focused during each commit, then all of the following before final delivery:

```powershell
.\.venv\Scripts\python.exe -m pytest clio/tests -q --cov=clio --cov-report=term
npm test
ruff check clio main.py
ruff format --check clio main.py
.\.venv\Scripts\mypy.exe --check-untyped-defs --show-error-codes
.\.venv\Scripts\python.exe main.py check
```

Manual smoke checks:

- Disable/enable desktop notifications and confirm only hidden-page notifications are native.
- Trigger the ffmpeg dialog on a machine with explicit paths and confirm no overwrite.
- Preview a day with cut clips, delete one cut, and confirm source fallback.
- Switch zh-CN/en after reload and confirm `<html lang>`, settings, warnings, and notifications update.

## Acceptance Criteria

- R-042 has no duplicate native notification for a visible page, and inbox behavior is unchanged.
- Installer never accepts unsigned/unverified archives and never overwrites explicit paths.
- R-031b uses cut clips when valid and falls back without a playback dead end.
- I18n fallback is deterministic, resource files ship, and language persists across restarts.
- All existing Python/Vitest tests pass; new security and fallback paths have focused tests.
- Every listed slice has its own commit and push, with CI monitored after the final push.

## Risks and Rollback

- Native notification APIs vary by OS: keep them optional and additive; disable by setting the flag false.
- Rolling ffmpeg assets can change: checksums are fetched from the same release, and package managers stay primary.
- Cut files can be stale: verify metadata and retry source fallback.
- Translation churn can hide regressions: migrate in small groups with key-existence tests and a remaining-string inventory.
- If CI reveals a platform-specific failure, revert only the affected slice; other slices remain independently deployable.

## Documentation Updates

- `config.example.yaml`, `docs/project.example.yaml` (only if a project field is added; `ui` is global-only).
- `README.md` for user-facing install/notification/language behavior.
- `AGENTS.md` conventions for the installer, notification delivery, preview mapping, and i18n.
- `ROADMAP.md` completion rows after each feature slice.
- `CHANGELOG.md` entry only after the complete set passes CI.
