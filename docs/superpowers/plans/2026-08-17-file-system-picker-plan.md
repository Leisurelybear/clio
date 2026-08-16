# Unified File-System Picker Plan

Status: Complete. Verified with 1880 Python tests (12 skipped), 480 Vitest
tests, Ruff, mypy, and a live local HTTP smoke check.

## Problem

The UI renders browse buttons for project, output, executable, template, cut,
and relink paths, but `desktop-pick.js` hides every browse button outside the
pywebview shell. Browser mode therefore falls back to manual absolute-path
entry even though authenticated filesystem listing routes already exist.

Desktop selection currently opens Tk dialogs from a pywebview API worker. This
works on some systems but can lose focus or block behind the WebView window.

## Design

1. Keep a single frontend picker contract: folder, single file, and multiple files.
2. In desktop mode, call pywebview's window-native file dialog first and retain
   Tk as a compatibility fallback.
3. In browser mode, open an in-app modal backed by an authenticated filesystem
   entries route. Paths always refer to the machine running Clio; browser file
   upload is intentionally out of scope.
4. Preserve manual path entry for recovery and advanced use.
5. Extend reveal behavior to select files in Explorer/Finder (or open the parent
   directory on Linux), then expose that action from video menus.

## Security

- Reuse `_is_allowed_path()` and route token authentication.
- Return metadata only; never return file contents from the picker route.
- Keep hidden entries excluded and validate directory creation as before.
- LAN clients browse the server filesystem, not the client filesystem.

## Verification

- Python route, reveal, desktop API, and router tests.
- Vitest coverage for picker fallback, entries modal helpers, and video menu actions.
- Focused Python and frontend suites, followed by formatting/lint checks for touched files.
