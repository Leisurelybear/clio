# Clio — Desktop Shell (pywebview)

> Series: `11` of `00-overview.md`. Covers `clio/desktop/` — the native window host,
> non-blocking server lifecycle, single-instance coordination, and native folder/file pickers.

## 1. Responsibilities

- Launch the app as a native desktop window (pywebview → WebView2/WebKit) that loads the
  embedded web UI from a localhost server — CLI-feel but with a real desktop shell.
- Keep exactly **one** Clio instance per config dir (`clio.lock`); a second launch focuses the
  first.
- Enforce a safe close policy (cancel active tasks before quitting) and persist last-used
  folder in `desktop-state.json`.

## 2. Module Map

| File | Responsibility |
|---|---|
| `app.py` | `main` (`:136`) — entrypoint: resolve config (`resolve_desktop_config_path`:95), load config, single-instance probe, `start_server`, create webview window at `/?token=…`, focus callback, closing/closed events, tidy shutdown in `finally` |
| `server_host.py` | `start_server` (`:31`) / `stop_server` (`:82`) — non-blocking `BoundedThreadingHTTPServer` on a background thread; `fetch_active_tasks` (`:93`), `request_task_cancel` (`:109`), `request_run_cancel` (`:127`) used by the close policy |
| `single_instance.py` | `clio.lock` JSON in config dir: `read_lock`/`write_lock` (atomic `os.replace` + per-launch token)/`remove_lock` (owner-checked)/`owns_lock` (`:25-94`); liveness via `focus_first_instance` (`:97`, HTTP 200 *or any* HTTPError ⇒ alive); `is_web_running` (`:119`) probes the 8765 web UI marker |
| `state.py` | `desktop-state.json` — `load_last_dir`/`save_last_dir`/`resolve_initial_dir` so the picker opens at the last used folder |
| `api.py` | `DesktopApi` (`:20`) — `js_api` bridge exposing `pick_folder`/`pick_file`/`pick_files` to the webview page, returning normalized envelope dicts |
| `dialogs.py` | Native dialog wrappers (tkinter) + `envelope_path(s)`/`envelope_error` for the JS API |

## 3. Startup Sequence (`app.py:main`)

```text
resolve_desktop_config_path(argv, -c)      # explicit -c → CWD config.yaml → platform dir
  ├─ config dir: win %APPDATA%\Clio | mac ~/Library/Application Support/Clio | linux ~/.config/clio
load_config (auto-creates config.yaml from bundled example; B-1/R-040)
setup_logging(cfg.paths.logs_dir)
instance_token = secrets.token_hex(16)
read_lock → focus_first_instance(port)?  → focus original window, exit 0
is_web_running(8765)?                    → confirm dialog, else continue
handle = start_server(host=127.0.0.1, port=0, api_token=None → fresh random token)
url = http://{host}:{port}/?token={token} → pywebview window (1280×800, text_select)
  ├─ DesktopApi(config_dir) as window.js_api (native pickers)
  ├─ set_desktop_focus_callback(window.restore/show)  → /api/desktop/focus target
  └─ write_lock(config_dir, port, pid, token=instance_token)
webview.start()
finally: stop_server(handle); remove_lock(config_dir, token=instance_token)
```

Server startup mirrors `clio.ui.server.run` (token, `resolve_last_project_config`,
`auto_reindex_if_needed`, `make_handler`) but **never opens a browser and never blocks**.
`enforce_local_session=True` binds the peer to loopback hosts.

## 4. Single-Instance & Liveness (R-039)

- The lock records `{port, pid, token}`. Live-ness is decided by **HTTP**, not PID — a second
  instance asks the first to focus via `POST /api/desktop/focus`:
  - `200` or any `HTTPError` ⇒ first instance alive → focus + exit.
  - `OSError` (unreachable) ⇒ stale lock → takeover.
- The per-launch `token` protects removal: `remove_lock` only deletes a lock it owns, so a stale
  takeover can never delete a *newer* live instance’s lock.
- `is_web_running` checks the standalone `serve` web UI (default 8765) by peeking its HTML
  marker; if a browser-only instance is up, the user is asked whether to continue in parallel.

## 5. Close Policy

`_on_closing` → `_handle_closing` (`:75`): probe `GET /api/tasks` for queued/running/cancelling
tasks; if any, ask via native confirm dialog, and on confirm **cancel every active task**
(`request_run_cancel`) before the window closes. `stop_server` then shuts the HTTP server,
joins the thread, `shutdown_task_manager`, and runs `before_stop()` hooks.

## 6. JS API / Pickers

- The webview page calls `pywebview.api.pick_folder / pick_file / pick_files(...)`; `DesktopApi`
  maps them onto `dialogs.py` native dialogs, constrained by config-scope allowed roots
  (`_native_paths`) and the requested kind’s extension filter.
- Results return `envelope_path(s)` dicts `{path: string|null, canceled: bool}` (files →
  `{paths: []}`), or `{error: message}` — the frontend `desktop-pick.js` consumes this uniform
  shape and `setBrowseButtonsVisible` swaps server picker vs native picker in browser mode.

## 7. Cross-Module Dependencies

- → `clio/ui/server.py` (`make_handler`, `set_desktop_focus_callback`, `shutdown_task_manager`)
- → `clio/ui/http_server.py` (`BoundedThreadingHTTPServer`)
- → `clio/ui/services/project_service.py` (`resolve_last_project_config`)
- → `clio/tasks/reindex.py` (`auto_reindex_if_needed`)
- → `clio/config` (`load_config`, auto-create from example), `clio/log` (`setup_logging`),
  `clio/shutdown` (`install_hooks`/`before_stop`)
- → pywebview (third-party, optional import — degraded to CLI when absent)

## 8. Risks / Maintenance Notes

1. `webview.confirm_dialog` and the tkinter fallback both fail closed (returns True) on error —
   an unavailable dialog must never block a user-initiated quit, but the cancellation path
   (`confirm_quit()` returns False) aborts the close; the two must stay visually distinguishable.
2. `focus_first_instance` treats **any** HTTPError as alive — a 500 from a half-starting server
   focuses instead of taking over; correct for R-039 but means the lock can only be reclaimed
   when the distant server is fully unreachable.
3. The desktop server always binds loopback with a fresh token, but `start_server` has the
   `api_token` escape hatch — if a caller passes a fixed token (e.g. debugging) the session
   boundary weakens.
4. `stop_server` waits `timeout=5s` then gives up on the server thread; very long blocked
   requests can outlive the window and hold the `.clio` DB lock briefly on Windows.
5. pywebview is optional (import guarded); `clio desktop` on a machine without WebView2 shows a
   startup-error dialog with install guidance, but there is no silent CLI-fallback mode — a
   headless run intentionally errors rather than behaving like `serve`.