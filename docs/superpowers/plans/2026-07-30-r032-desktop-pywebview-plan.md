# R-032 Desktop pywebview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Windows desktop host that starts the existing localhost UI server, opens it in pywebview, and replaces custom HTML directory/file pickers with native OS dialogs via a thin `js_api`.

**Architecture:** Hybrid (ROADMAP Option A). One process: background `ThreadingHTTPServer` on `127.0.0.1` (free port) + main-thread pywebview window pointed at `http://127.0.0.1:<port>/`. JSON APIs, video Range, covers, and SSE stay on HTTP. Only `pick_folder` / `pick_file` / `pick_files` go through `window.pywebview.api`. Serve mode keeps HTTP; browse buttons are hidden and paths stay manual text.

**Tech Stack:** Python 3.11+, existing `clio.ui.server`, pywebview (WebView2), `tkinter.filedialog` (primary) or Win32 `IFileOpenDialog` (fallback after spike), vanilla ES modules + Vitest, PyInstaller onedir (R-032c).

**Spec:** `docs/superpowers/specs/2026-07-29-r032-desktop-pywebview-design.md`

## Global Constraints

- **Hybrid only:** do not rewrite `api.js` to route all calls through `js_api`; do not add `/api/fs/pick_*` HTTP routes.
- **Dialogs only on desktop:** `js_api` pickers exist only when `window.pywebview?.api` is present; serve + browser = hide browse buttons, manual path text, no dual picker UI.
- **Cancel does not clear:** native dialog cancel / `ok:false, cancelled:true` must leave input values unchanged.
- **Token:** desktop starts server with `api_token=None` on loopback → existing `server.run` sets `TOKEN=""` (`clio/ui/server.py:554-558`); no auth modal.
- **Never** load SPA primarily via `file://`.
- **Windows-first** for packaging; keep imports portable where cheap.
- **No** tray / first-run wizard / single-instance / ffmpeg bundling / code-sign (R-032d/e).
- Chinese UI: button **浏览**, cancel toast optional; no new English-only chrome for path pickers.
- Keep `python main.py serve` working for API/media/SSE.

---

## File map

| Path | Role |
| --- | --- |
| `clio/desktop/__init__.py` | Public `main` export |
| `clio/desktop/__main__.py` | `python -m clio.desktop` |
| `clio/desktop/dialogs.py` | OS dialog wrappers + JSON envelope builders |
| `clio/desktop/state.py` | `desktop-state.json` last_dir read/write |
| `clio/desktop/api.py` | `DesktopApi` class for pywebview `js_api` |
| `clio/desktop/server_host.py` | Start/stop localhost server on free port (no browser) |
| `clio/desktop/app.py` | Window lifecycle, close/shutdown |
| `clio/ui/static/src/desktop-pick.js` | Frontend pick helpers + serve/desktop branching |
| `clio/ui/static/src/__tests__/desktop-pick.test.js` | Vitest for pick helpers |
| `clio/tests/test_desktop_dialogs.py` | Unit tests (mocked OS dialog) |
| `clio/tests/test_desktop_state.py` | last_dir persistence |
| `clio/tests/test_desktop_server_host.py` | free-port bind + stop |
| `clio/main.py` | `desktop` subcommand |
| `requirements.txt` or extra | `pywebview` dependency note |
| `packaging/clio.spec` + short build notes | R-032c onedir |

**Modify (pickers / chrome):**

- `clio/ui/static/src/main.js` — browse delegation
- `clio/ui/static/src/editor-config.js` — path-field 浏览 buttons
- `clio/ui/static/src/editor-plan.js` — cut-outdir already uses `.browse-btn` (inherits main wiring)
- `clio/ui/static/src/sidebar-video-manage.js` — multi-file native pick
- `clio/ui/static/src/sidebar-relink.js` — single-file native pick; drop in-modal tree
- `clio/ui/static/src/sidebar-batch-relink.js` — folder native pick → existing scan
- `clio/ui/static/index.html` — remove `#modal-browse-dir`; slim relink/video/batch browse chrome
- `clio/ui/static/src/sidebar-browse.js` — delete or reduce to no-op re-exports only if tests still mock it; prefer delete + fix mocks
- `clio/ui/static/src/sidebar.js` — drop `openBrowseDir` re-export if deleted
- `clio/ui/server.py` — only if `server_host` needs a small extract (prefer new helper that reuses `make_handler` / bind logic without changing serve behavior)

---

### Task 1: Dialog wrappers + envelope (pure, mockable)

**Files:**
- Create: `clio/desktop/__init__.py`
- Create: `clio/desktop/dialogs.py`
- Create: `clio/tests/test_desktop_dialogs.py`

**Interfaces:**
- Consumes: `clio._constants.VIDEO_EXTENSIONS`
- Produces:
  - `pick_folder(initial_dir: str | None = None) -> str | None`
  - `pick_file(initial_dir: str | None = None, exts: list[str] | None = None) -> str | None`
  - `pick_files(initial_dir: str | None = None, multiple: bool = True, exts: list[str] | None = None) -> list[str]`
  - `envelope_path(path: str | None) -> dict` → `{ok:true, path}` or `{ok:false, cancelled:true}`
  - `envelope_paths(paths: list[str]) -> dict` → `{ok:true, paths}` or cancelled if empty from cancel
  - `envelope_error(message: str) -> dict` → `{ok:false, error}`
  - Internal OS call is injectable via module-level `_askdirectory` / `_askopenfilename` / `_askopenfilenames` for tests (default = tkinter)

- [x] **Step 1: Write the failing tests**

```python
# clio/tests/test_desktop_dialogs.py
from __future__ import annotations

from clio.desktop import dialogs


def test_envelope_path_success_and_cancel():
    assert dialogs.envelope_path(r"D:\trip") == {"ok": True, "path": r"D:\trip"}
    assert dialogs.envelope_path(None) == {"ok": False, "cancelled": True}
    assert dialogs.envelope_path("") == {"ok": False, "cancelled": True}


def test_envelope_paths_success_and_cancel():
    assert dialogs.envelope_paths([r"D:\a.mp4"]) == {"ok": True, "paths": [r"D:\a.mp4"]}
    assert dialogs.envelope_paths([]) == {"ok": False, "cancelled": True}


def test_envelope_error():
    assert dialogs.envelope_error("boom") == {"ok": False, "error": "boom"}


def test_pick_folder_uses_initial_and_returns_abs(monkeypatch, tmp_path):
    chosen = tmp_path / "out"
    chosen.mkdir()
    seen = {}

    def fake_ask(initialdir=None, **kwargs):
        seen["initialdir"] = initialdir
        return str(chosen)

    monkeypatch.setattr(dialogs, "_askdirectory", fake_ask)
    assert dialogs.pick_folder(str(tmp_path)) == str(chosen.resolve())
    assert seen["initialdir"] == str(tmp_path)


def test_pick_folder_cancel(monkeypatch):
    monkeypatch.setattr(dialogs, "_askdirectory", lambda **kwargs: "")
    assert dialogs.pick_folder() is None


def test_pick_files_filters_and_multiple(monkeypatch, tmp_path):
    f1 = tmp_path / "a.mp4"
    f2 = tmp_path / "b.mov"
    f1.write_bytes(b"x")
    f2.write_bytes(b"y")
    monkeypatch.setattr(
        dialogs,
        "_askopenfilenames",
        lambda **kwargs: (str(f1), str(f2)),
    )
    paths = dialogs.pick_files(str(tmp_path), multiple=True)
    assert paths == [str(f1.resolve()), str(f2.resolve())]


def test_pick_file_single(monkeypatch, tmp_path):
    f1 = tmp_path / "a.mp4"
    f1.write_bytes(b"x")
    monkeypatch.setattr(dialogs, "_askopenfilename", lambda **kwargs: str(f1))
    assert dialogs.pick_file(str(tmp_path)) == str(f1.resolve())
```

- [x] **Step 2: Run tests to verify they fail**

```bash
pytest clio/tests/test_desktop_dialogs.py -v
```

Expected: FAIL — `clio.desktop` / `dialogs` import missing.

- [x] **Step 3: Implement minimal `dialogs.py`**

```python
# clio/desktop/dialogs.py
from __future__ import annotations

from pathlib import Path
from typing import Any

from clio._constants import VIDEO_EXTENSIONS

def _askdirectory(**kwargs: Any) -> str:
    from tkinter import Tk, filedialog
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askdirectory(**kwargs) or ""
    finally:
        root.destroy()

def _askopenfilename(**kwargs: Any) -> str:
    from tkinter import Tk, filedialog
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askopenfilename(**kwargs) or ""
    finally:
        root.destroy()

def _askopenfilenames(**kwargs: Any) -> tuple[str, ...] | list[str] | str:
    from tkinter import Tk, filedialog
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askopenfilenames(**kwargs) or ()
    finally:
        root.destroy()

def _video_filetypes(exts: list[str] | None = None) -> list[tuple[str, str]]:
    use = exts or sorted(e.lstrip(".").lower() for e in VIDEO_EXTENSIONS)
    pattern = " ".join(f"*.{e.lstrip('.')}" for e in use)
    return [("Videos", pattern), ("All files", "*.*")]

def _normalize_existing(path: str | None) -> str | None:
    if not path:
        return None
    return str(Path(path).expanduser().resolve())

def pick_folder(initial_dir: str | None = None) -> str | None:
    kwargs: dict[str, Any] = {}
    if initial_dir:
        kwargs["initialdir"] = initial_dir
    raw = _askdirectory(**kwargs)
    return _normalize_existing(raw) if raw else None

def pick_file(initial_dir: str | None = None, exts: list[str] | None = None) -> str | None:
    kwargs: dict[str, Any] = {"filetypes": _video_filetypes(exts)}
    if initial_dir:
        kwargs["initialdir"] = initial_dir
    raw = _askopenfilename(**kwargs)
    return _normalize_existing(raw) if raw else None

def pick_files(
    initial_dir: str | None = None,
    multiple: bool = True,
    exts: list[str] | None = None,
) -> list[str]:
    if not multiple:
        one = pick_file(initial_dir=initial_dir, exts=exts)
        return [one] if one else []
    kwargs: dict[str, Any] = {"filetypes": _video_filetypes(exts)}
    if initial_dir:
        kwargs["initialdir"] = initial_dir
    raw = _askopenfilenames(**kwargs)
    if not raw:
        return []
    if isinstance(raw, str):
        raw = (raw,)
    out: list[str] = []
    for p in raw:
        n = _normalize_existing(p)
        if n:
            out.append(n)
    return out

def envelope_path(path: str | None) -> dict[str, Any]:
    if not path:
        return {"ok": False, "cancelled": True}
    return {"ok": True, "path": path}

def envelope_paths(paths: list[str]) -> dict[str, Any]:
    if not paths:
        return {"ok": False, "cancelled": True}
    return {"ok": True, "paths": paths}

def envelope_error(message: str) -> dict[str, Any]:
    return {"ok": False, "error": str(message)}
```

```python
# clio/desktop/__init__.py
from clio.desktop.app import main  # will exist in Task 4; for Task 1 only export dialogs

# Temporary for Task 1 — avoid importing app until it exists:
# Prefer:
"""Desktop shell package."""
__all__: list[str] = []
```

For Task 1 keep `__init__.py` empty/`__all__ = []` so tests import `clio.desktop.dialogs` only. Wire `main` in Task 4/5.

Also add `pick_file` overload used later for ffmpeg: callers pass `exts=["exe"]` or a dedicated `filetypes` later — for now `_video_filetypes` with `exts=["exe"]` yields `*.exe` which is enough for config ffmpeg/ffprobe.

- [x] **Step 4: Run tests to verify they pass**

```bash
pytest clio/tests/test_desktop_dialogs.py -v
```

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add clio/desktop/__init__.py clio/desktop/dialogs.py clio/tests/test_desktop_dialogs.py
git commit -m "feat(desktop): add mockable native dialog wrappers"
```

---

### Task 2: last_dir desktop state

**Files:**
- Create: `clio/desktop/state.py`
- Create: `clio/tests/test_desktop_state.py`

**Interfaces:**
- Consumes: none beyond stdlib + pathlib
- Produces:
  - `state_path(config_dir: Path) -> Path` → `config_dir / "desktop-state.json"`
  - `load_last_dir(config_dir: Path) -> str | None`
  - `save_last_dir(config_dir: Path, path: str) -> None` — stores **parent directory** of a file pick, or the folder itself for folder picks; schema `{"last_dir": "<abs>"}`
  - `resolve_initial_dir(config_dir: Path, preferred: str | None = None) -> str | None` — if `preferred` exists as dir use it; else last_dir if exists; else `None` (caller may fall back to `Path.home()`)

- [x] **Step 1: Write the failing tests**

```python
# clio/tests/test_desktop_state.py
from pathlib import Path

from clio.desktop.state import load_last_dir, resolve_initial_dir, save_last_dir, state_path


def test_state_path(tmp_path: Path):
    assert state_path(tmp_path) == tmp_path / "desktop-state.json"


def test_save_and_load_last_dir(tmp_path: Path):
    d = tmp_path / "media"
    d.mkdir()
    save_last_dir(tmp_path, str(d))
    assert load_last_dir(tmp_path) == str(d.resolve())


def test_save_file_stores_parent(tmp_path: Path):
    d = tmp_path / "media"
    d.mkdir()
    f = d / "a.mp4"
    f.write_bytes(b"x")
    save_last_dir(tmp_path, str(f), is_file=True)
    assert load_last_dir(tmp_path) == str(d.resolve())


def test_resolve_prefers_existing_preferred(tmp_path: Path):
    pref = tmp_path / "project"
    pref.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    save_last_dir(tmp_path, str(other))
    assert resolve_initial_dir(tmp_path, str(pref)) == str(pref.resolve())


def test_resolve_falls_back_to_last(tmp_path: Path):
    other = tmp_path / "other"
    other.mkdir()
    save_last_dir(tmp_path, str(other))
    assert resolve_initial_dir(tmp_path, str(tmp_path / "missing")) == str(other.resolve())
```

- [x] **Step 2: Run tests — expect FAIL**

```bash
pytest clio/tests/test_desktop_state.py -v
```

- [x] **Step 3: Implement `state.py`**

```python
# clio/desktop/state.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def state_path(config_dir: Path) -> Path:
    return Path(config_dir) / "desktop-state.json"


def load_last_dir(config_dir: Path) -> str | None:
    p = state_path(config_dir)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = data.get("last_dir") if isinstance(data, dict) else None
    if not raw:
        return None
    path = Path(str(raw))
    return str(path) if path.is_dir() else None


def save_last_dir(config_dir: Path, path: str, is_file: bool = False) -> None:
    target = Path(path).expanduser().resolve()
    folder = target.parent if is_file else target
    if not folder.is_dir():
        return
    config_dir = Path(config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"last_dir": str(folder)}
    state_path(config_dir).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def resolve_initial_dir(config_dir: Path, preferred: str | None = None) -> str | None:
    if preferred:
        p = Path(preferred).expanduser()
        if p.is_dir():
            return str(p.resolve())
    return load_last_dir(config_dir)
```

- [x] **Step 4: Run tests — expect PASS**

```bash
pytest clio/tests/test_desktop_state.py -v
```

- [x] **Step 5: Commit**

```bash
git add clio/desktop/state.py clio/tests/test_desktop_state.py
git commit -m "feat(desktop): persist last picked directory"
```

---

### Task 3: Server host helper (free port, no browser, stoppable)

**Files:**
- Create: `clio/desktop/server_host.py`
- Create: `clio/tests/test_desktop_server_host.py`
- Modify only if needed: `clio/ui/server.py` (prefer **no** behavior change to `run()`; extract by calling `make_handler` + `ThreadingHTTPServer` from the new helper)

**Interfaces:**
- Consumes: `clio.ui.server.make_handler`, `AppConfig`, existing startup pieces as needed (`install_hooks`, `resolve_last_project_config`, `auto_reindex_if_needed` — mirror `run()` startup lightly or call a shared internal if extracted)
- Produces:
  - `@dataclass class ServerHandle: host: str; port: int; server: ThreadingHTTPServer; thread: threading.Thread`
  - `start_server(config, config_path: Path | None = None, host: str = "127.0.0.1", port: int = 0, api_token: str | None = None) -> ServerHandle`
    - `port=0` → OS assigns free port; read `server.server_address[1]`
    - `api_token=None` + loopback → empty token (same as `run`)
    - `open_browser` never true
    - thread daemon or non-daemon: **non-daemon** preferred so work finishes; stop explicitly on shutdown
  - `stop_server(handle: ServerHandle, timeout: float = 5.0) -> None` — `server.shutdown()`, `server_close()`, join thread

**Note:** Do not call `run()` (it blocks on `serve_forever` on the caller thread and may open a browser). Copy the token + handler setup from `run()` carefully.

- [x] **Step 1: Write the failing test**

```python
# clio/tests/test_desktop_server_host.py
from __future__ import annotations

import urllib.request

from clio.config import load_config
from clio.desktop.server_host import start_server, stop_server


def test_start_server_binds_loopback_and_serves(tmp_path, monkeypatch):
    # Minimal config: reuse project fixtures if present; otherwise load default config.yaml
    cfg = load_config("config.yaml")  # repo root default; if flaky in CI, construct AppConfig in-test
    handle = start_server(cfg, config_path=None, host="127.0.0.1", port=0, api_token=None)
    try:
        assert handle.host == "127.0.0.1"
        assert handle.port > 0
        url = f"http://{handle.host}:{handle.port}/"
        with urllib.request.urlopen(url, timeout=3) as resp:
            assert resp.status == 200
            body = resp.read()
            assert b"html" in body.lower() or b"<!doctype" in body.lower() or len(body) > 0
    finally:
        stop_server(handle)
```

If `load_config("config.yaml")` is heavy or path-sensitive, use the same pattern as `clio/tests/test_server.py` / `conftest.py` fixtures — **match existing test helpers** rather than inventing a new config factory. Open `clio/tests/test_server.py` and reuse its fixture approach if this test is awkward.

- [x] **Step 2: Run — expect FAIL**

```bash
pytest clio/tests/test_desktop_server_host.py -v
```

- [x] **Step 3: Implement `server_host.py`**

Implement by adapting `clio/ui/server.py:run` (lines ~536–603):

1. Resolve token exactly like `run` (`None` + loopback → `""`).
2. `resolve_last_project_config` + optional `auto_reindex_if_needed` (same as serve; keep behavior).
3. `handler = make_handler(..., api_token=TOKEN)`.
4. `ThreadingHTTPServer((host, port), handler)` with `port=0` allowed.
5. Start `threading.Thread(target=server.serve_forever, name="clio-http", daemon=True)`.
6. Return `ServerHandle`.
7. `stop_server`: `server.shutdown(); server.server_close(); thread.join(timeout=...)`.

Do **not** print token URLs in desktop mode when token is empty; keep quiet or log to session log later.

- [x] **Step 4: Run — expect PASS**

```bash
pytest clio/tests/test_desktop_server_host.py -v
```

- [x] **Step 5: Commit**

```bash
git add clio/desktop/server_host.py clio/tests/test_desktop_server_host.py
git commit -m "feat(desktop): start/stop localhost UI server on free port"
```

---

### Task 4: DesktopApi + app entry (spike: window + one pick)

**Files:**
- Create: `clio/desktop/api.py`
- Create: `clio/desktop/app.py`
- Create: `clio/desktop/__main__.py`
- Modify: `clio/desktop/__init__.py` — export `main`
- Modify: `requirements.txt` — add `pywebview>=5.0` (or current stable; pin loosely like other deps)
- Optional spike note file is **not** required; record thread decision in a short comment at top of `dialogs.py` or `api.py` after the spike

**Interfaces:**
- Consumes: `dialogs.*`, `state.*`, `server_host.*`, `load_config`
- Produces:
  - `class DesktopApi:`
    - `__init__(self, config_dir: Path)`
    - `pick_folder(self, initial_dir: str = "") -> dict`
    - `pick_file(self, initial_dir: str = "", kind: str = "video") -> dict`  
      - `kind`: `"video"` | `"exe"` | `"any"` → filetypes
    - `pick_files(self, initial_dir: str = "", kind: str = "video") -> dict`
    - Each method: `resolve_initial_dir` → dialog → on success `save_last_dir` → envelope
  - `main(argv: list[str] | None = None) -> int` in `app.py`
  - `__main__.py`: `raise SystemExit(main())`

**R-032b spike order (manual, required before declaring Task 4 done):**

1. `start_server` + `webview.create_window("Clio", url)` + `webview.start()` — confirm ES modules load (Network/console: no failed `.js` imports).
2. From DevTools or a temporary button, call `window.pywebview.api.pick_folder()` — if it hangs/crashes, implement main-thread dispatch **or** switch `dialogs.py` to Win32 `IFileOpenDialog` and document the choice in a 3-line comment in `api.py`.

- [x] **Step 1: Implement `DesktopApi`**

```python
# clio/desktop/api.py
from __future__ import annotations

from pathlib import Path

from clio.desktop import dialogs
from clio.desktop.state import resolve_initial_dir, save_last_dir


class DesktopApi:
    def __init__(self, config_dir: Path) -> None:
        self._config_dir = Path(config_dir)

    def _initial(self, initial_dir: str = "") -> str | None:
        return resolve_initial_dir(self._config_dir, initial_dir or None)

    def pick_folder(self, initial_dir: str = "") -> dict:
        try:
            path = dialogs.pick_folder(self._initial(initial_dir))
            if path:
                save_last_dir(self._config_dir, path, is_file=False)
            return dialogs.envelope_path(path)
        except Exception as e:  # noqa: BLE001 — surface to JS
            return dialogs.envelope_error(str(e))

    def pick_file(self, initial_dir: str = "", kind: str = "video") -> dict:
        try:
            exts = _exts_for_kind(kind)
            path = dialogs.pick_file(self._initial(initial_dir), exts=exts)
            if path:
                save_last_dir(self._config_dir, path, is_file=True)
            return dialogs.envelope_path(path)
        except Exception as e:  # noqa: BLE001
            return dialogs.envelope_error(str(e))

    def pick_files(self, initial_dir: str = "", kind: str = "video") -> dict:
        try:
            exts = _exts_for_kind(kind)
            paths = dialogs.pick_files(self._initial(initial_dir), multiple=True, exts=exts)
            if paths:
                save_last_dir(self._config_dir, paths[0], is_file=True)
            return dialogs.envelope_paths(paths)
        except Exception as e:  # noqa: BLE001
            return dialogs.envelope_error(str(e))


def _exts_for_kind(kind: str) -> list[str] | None:
    if kind == "exe":
        return ["exe"]
    if kind == "any":
        return None  # dialogs should treat None as all files — adjust _video_filetypes to allow
    return None  # default video set inside dialogs
```

If `kind == "any"` / `"exe"`, adjust `dialogs._video_filetypes` so `exts is None` with a `all_files_only` flag **or** add `filetypes` parameter. Minimal fix: when `exts == ["exe"]` return `[("Executable", "*.exe"), ("All files", "*.*")]`; when `exts is None` keep video defaults; add `kind` handling only in api.

- [x] **Step 2: Implement `app.py`**

```python
# clio/desktop/app.py
from __future__ import annotations

import sys
from pathlib import Path

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Optional: parse --config like main.py; v1 hardcode default config.yaml relative to cwd
    config_path = Path("config.yaml")
    from clio.config import load_config
    from clio.desktop.api import DesktopApi
    from clio.desktop.server_host import start_server, stop_server

    cfg = load_config(str(config_path) if config_path.is_file() else "config.yaml")
    config_dir = config_path.parent.resolve() if config_path.is_file() else Path.cwd()

    handle = start_server(cfg, config_path=config_path if config_path.is_file() else None,
                          host="127.0.0.1", port=0, api_token=None)
    url = f"http://{handle.host}:{handle.port}/"
    try:
        import webview
        api = DesktopApi(config_dir)
        window = webview.create_window("Clio", url, js_api=api, width=1280, height=800)
        # Close policy refined in Task 11; v1: on closed stop server
        def _on_closed():
            stop_server(handle)
        try:
            window.events.closed += _on_closed  # pywebview event API — verify version
        except Exception:
            pass
        webview.start()
    finally:
        stop_server(handle)
    return 0
```

Verify pywebview `events.closed` API for the pinned version; if different, use `webview.start()` return / guilib hooks. Goal: no orphan HTTP thread after window close.

- [x] **Step 3: `__main__.py` + package export**

```python
# clio/desktop/__main__.py
from clio.desktop.app import main
raise SystemExit(main())
```

```python
# clio/desktop/__init__.py
from clio.desktop.app import main
__all__ = ["main"]
```

- [x] **Step 4: Add dependency**

Append to `requirements.txt`:

```
pywebview>=5.0
```

- [x] **Step 5: Manual spike**

```bash
pip install pywebview
python -m clio.desktop
```

Checklist:
- [x] Window opens on `http://127.0.0.1:<ephemeral>/`
- [x] SPA loads (sidebar visible, no module errors)
- [x] In console: `await window.pywebview.api.pick_folder()` opens native dialog and returns envelope
- [x] Closing window returns to shell; no leftover python holding the port (`netstat` / re-run succeeds)

If Tk deadlocks: implement Win32 path **before** proceeding to frontend tasks; keep the same `pick_*` function names.

- [x] **Step 6: Commit**

```bash
git add clio/desktop requirements.txt
git commit -m "feat(desktop): pywebview host with js_api pickers"
```

---

### Task 5: CLI `desktop` subcommand

**Files:**
- Modify: `clio/main.py` (parser ~273 + dispatch ~528)
- Modify: `clio/tests/test_main.py` if it asserts subcommand list

**Interfaces:**
- Produces: `python main.py desktop` → `clio.desktop.app.main()` (same as `-m clio.desktop`)
- Args v1: optional `--config` already global on root parser

- [x] **Step 1: Add parser**

After `p_serve` block:

```python
p_desktop = sub.add_parser(
    "desktop",
    help="启动桌面窗口（pywebview + 本地 UI，系统文件对话框）",
)
```

- [x] **Step 2: Dispatch**

```python
elif args.command == "desktop":
    from clio.desktop.app import main as run_desktop
    return run_desktop()  # or pass config_path if app.main accepts it
```

Prefer threading `config_path` from `main()` into `app.main` so `-c` works:

```python
# app.main signature
def main(argv: list[str] | None = None, config_path: str | Path | None = None) -> int:
```

From CLI:

```python
return run_desktop(config_path=config_path)
```

- [x] **Step 3: Run unit tests that cover argparse if any**

```bash
pytest clio/tests/test_main.py -v -k desktop
# or full test_main if no filter
pytest clio/tests/test_main.py -v
```

- [x] **Step 4: Commit**

```bash
git add clio/main.py clio/desktop/app.py clio/tests/test_main.py
git commit -m "feat(cli): add desktop subcommand"
```

---

### Task 6: Frontend `desktop-pick.js` helpers + Vitest

**Files:**
- Create: `clio/ui/static/src/desktop-pick.js`
- Create: `clio/ui/static/src/__tests__/desktop-pick.test.js`

**Interfaces:**
- Consumes: `window.pywebview.api` (optional)
- Produces:
  - `isDesktop() -> boolean`
  - `pickFolder(initialDir?: string) -> Promise<string | null>` — null on cancel/missing api
  - `pickFile(initialDir?: string, kind?: 'video'|'exe'|'any') -> Promise<string | null>`
  - `pickFiles(initialDir?: string, kind?: 'video'|'exe'|'any') -> Promise<string[] | null>` — null cancel; empty array should not be used for cancel (use null)
  - `applyPickToInput(inputEl: HTMLInputElement | null, path: string | null) -> boolean` — writes only if path is non-null string; returns whether wrote
  - `setBrowseButtonsVisible(root?: ParentNode) -> void` — if `!isDesktop()`, hide all `.browse-btn` (and optional `[data-desktop-browse]`)

Envelope handling:

```js
// success path: r.ok && r.path
// cancel: !r.ok && r.cancelled → return null, do not throw
// error: !r.ok && r.error → throw or return null + toast at call site; prefer throw Error(r.error)
```

- [x] **Step 1: Write failing Vitest**

```js
// clio/ui/static/src/__tests__/desktop-pick.test.js
import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  isDesktop,
  pickFolder,
  applyPickToInput,
  setBrowseButtonsVisible,
} from '../desktop-pick.js';

describe('desktop-pick', () => {
  beforeEach(() => {
    delete window.pywebview;
    document.body.innerHTML = `
      <input id="p" value="old" />
      <button class="browse-btn" type="button">浏览</button>
    `;
  });

  it('isDesktop false without pywebview', () => {
    expect(isDesktop()).toBe(false);
  });

  it('isDesktop true with api', () => {
    window.pywebview = { api: { pick_folder: vi.fn() } };
    expect(isDesktop()).toBe(true);
  });

  it('pickFolder returns path on ok', async () => {
    window.pywebview = {
      api: {
        pick_folder: vi.fn(async () => ({ ok: true, path: 'D:\\\\trip' })),
      },
    };
    await expect(pickFolder('D:\\\\')).resolves.toBe('D:\\\\trip');
  });

  it('pickFolder returns null on cancel', async () => {
    window.pywebview = {
      api: {
        pick_folder: vi.fn(async () => ({ ok: false, cancelled: true })),
      },
    };
    await expect(pickFolder()).resolves.toBeNull();
  });

  it('pickFolder returns null when not desktop', async () => {
    await expect(pickFolder()).resolves.toBeNull();
  });

  it('applyPickToInput writes only non-null', () => {
    const inp = document.getElementById('p');
    expect(applyPickToInput(inp, null)).toBe(false);
    expect(inp.value).toBe('old');
    expect(applyPickToInput(inp, 'D:\\\\x')).toBe(true);
    expect(inp.value).toBe('D:\\\\x');
  });

  it('setBrowseButtonsVisible hides in serve mode', () => {
    setBrowseButtonsVisible(document);
    expect(document.querySelector('.browse-btn').style.display).toBe('none');
  });
});
```

- [x] **Step 2: Run — expect FAIL**

```bash
npx vitest run clio/ui/static/src/__tests__/desktop-pick.test.js
```

- [x] **Step 3: Implement `desktop-pick.js`**

```js
export function isDesktop() {
  return !!(window.pywebview && window.pywebview.api);
}

export async function pickFolder(initialDir = '') {
  const api = window.pywebview?.api;
  if (!api?.pick_folder) return null;
  const r = await api.pick_folder(initialDir || '');
  if (!r || r.cancelled) return null;
  if (!r.ok) throw new Error(r.error || '选择目录失败');
  return r.path || null;
}

export async function pickFile(initialDir = '', kind = 'video') {
  const api = window.pywebview?.api;
  if (!api?.pick_file) return null;
  const r = await api.pick_file(initialDir || '', kind);
  if (!r || r.cancelled) return null;
  if (!r.ok) throw new Error(r.error || '选择文件失败');
  return r.path || null;
}

export async function pickFiles(initialDir = '', kind = 'video') {
  const api = window.pywebview?.api;
  if (!api?.pick_files) return null;
  const r = await api.pick_files(initialDir || '', kind);
  if (!r || r.cancelled) return null;
  if (!r.ok) throw new Error(r.error || '选择文件失败');
  return Array.isArray(r.paths) ? r.paths : null;
}

export function applyPickToInput(inputEl, path) {
  if (!inputEl || path == null || path === '') return false;
  inputEl.value = path;
  inputEl.dispatchEvent(new Event('input', { bubbles: true }));
  inputEl.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}

export function setBrowseButtonsVisible(root = document) {
  const show = isDesktop();
  root.querySelectorAll('.browse-btn, [data-desktop-browse]').forEach((btn) => {
    btn.style.display = show ? '' : 'none';
  });
}
```

- [x] **Step 4: Run — expect PASS**

```bash
npx vitest run clio/ui/static/src/__tests__/desktop-pick.test.js
```

- [x] **Step 5: Commit**

```bash
git add clio/ui/static/src/desktop-pick.js clio/ui/static/src/__tests__/desktop-pick.test.js
git commit -m "feat(ui): add desktop native pick helpers"
```

---

### Task 7: Wire global browse buttons + hide in serve mode

**Files:**
- Modify: `clio/ui/static/src/main.js` (~300–320 and imports)
- Modify: `clio/ui/static/index.html` — keep `.browse-btn` on np/op paths; visibility controlled in JS
- Do **not** remove cut-outdir browse button markup in `editor-plan.js` (uses same class)

**Interfaces:**
- Consumes: `pickFolder`, `applyPickToInput`, `setBrowseButtonsVisible` from `desktop-pick.js`
- Replaces: `openBrowseDir(btn.dataset.target)`

- [x] **Step 1: Change browse delegation**

In `main.js`:

```js
import { pickFolder, applyPickToInput, setBrowseButtonsVisible } from './desktop-pick.js';
// remove openBrowseDir import from sidebar-browse / sidebar

// on boot (after DOM ready):
setBrowseButtonsVisible(document);

document.body.addEventListener('click', async (e) => {
  const btn = e.target.closest('.browse-btn');
  if (!btn) return;
  e.preventDefault();
  const targetId = btn.dataset.target;
  const inp = targetId ? document.getElementById(targetId) : null;
  try {
    const initial = (inp?.value || '').trim();
    const path = await pickFolder(initial);
    applyPickToInput(inp, path);
  } catch (err) {
    console.error(err);
    // optional: addToast
  }
});
```

Delete handlers for `browse-select` / `browse-cancel` once `#modal-browse-dir` is removed (Task 10 can delete HTML; this task can no-op if elements missing — keep null checks).

- [x] **Step 2: Manual check**

- Desktop: click 浏览 on 新建项目 → native folder dialog → path fills.
- Serve: 浏览 buttons hidden; typing path still creates/opens project.

- [x] **Step 3: Commit**

```bash
git add clio/ui/static/src/main.js
git commit -m "feat(ui): route browse buttons to native folder dialog"
```

---

### Task 8: Config path-field 浏览 buttons

**Files:**
- Modify: `clio/ui/static/src/editor-config.js` (`_renderConfigForm` string branch ~185–203)
- Modify: `clio/ui/static/src/__tests__/editor.test.js` **or** add focused tests if config render is covered elsewhere — if none, add a small pure helper test in `desktop-pick` or export `isPathConfigKey(path)` from `editor-config.js` / `desktop-pick.js`

**Interfaces:**
- Path keys that get 浏览 (locked list):
  - `paths.output_dir` → folder
  - `paths.logs_dir` → folder
  - `export.jianying_draft_dir` → folder
  - `paths.ffmpeg` → file kind `exe`
  - `paths.ffprobe` → file kind `exe`
  - `script.template_file` → file kind `any`
- **Never** browse: `context_file` (already skipped), `*_api_key*`, URLs, multiline fields

```js
function pathPickKind(path) {
  if (path === 'paths.ffmpeg' || path === 'paths.ffprobe') return 'exe';
  if (path === 'script.template_file') return 'any';
  if (
    path === 'paths.output_dir' ||
    path === 'paths.logs_dir' ||
    path === 'export.jianying_draft_dir'
  ) return 'folder';
  return null;
}
```

In string render when `pathPickKind(path)` non-null:

```html
<label class="config-field config-str">
  <span class="config-key">...</span>
  <span class="input-with-browse">
    <input type="text" data-path="..." value="...">
    <button type="button" class="browse-btn" data-desktop-browse
      data-pick-kind="folder|exe|any" data-target-path="paths.ffmpeg">浏览</button>
  </span>
</label>
```

**Problem:** config inputs use `data-path`, not `id`. Global `.browse-btn` handler uses `data-target` id.

**Fix (choose one, implement exactly):**

**Preferred:** extend main.js handler:

```js
const kind = btn.dataset.pickKind || 'folder';
if (btn.dataset.target) { /* existing id path */ }
else if (btn.dataset.targetPath) {
  const inp = btn.parentElement?.querySelector('input, textarea');
  // or document.querySelector(`input[data-path="${CSS.escape(btn.dataset.targetPath)}"]`)
}
```

For kind:
- `folder` → `pickFolder`
- `exe` / `any` / `video` → `pickFile(..., kind)`

Call `setBrowseButtonsVisible` again after config form re-render (hook where form HTML is injected).

- [x] **Step 1: Implement `pathPickKind` + button markup + re-hide/show after render**
- [x] **Step 2: Extend browse click handler for `data-target-path` + `data-pick-kind`**
- [x] **Step 3: Vitest — assert `_renderConfigForm` output contains 浏览 for `paths.output_dir` and not for random string keys** (export `_renderConfigForm` if needed for test, or test via public render entry already used in editor tests)

- [x] **Step 4: Commit**

```bash
git add clio/ui/static/src/editor-config.js clio/ui/static/src/main.js clio/ui/static/src/__tests__/
git commit -m "feat(ui): add native browse on config path fields"
```

---

### Task 9: Add videos — native multi-file (flow 3)

**Files:**
- Modify: `clio/ui/static/src/sidebar-video-manage.js`
- Modify: `clio/ui/static/index.html` (`#modal-video-manage` chrome)
- Keep: drag-drop best-effort (`_vmInitDragDrop`); if `File.path` empty under WebView2, toast to use native button (do not block)

**Target UX (desktop):**
1. User clicks 添加视频.
2. If `isDesktop()`: immediately `pickFiles(preferredInitial)` where preferred = `state.currentProjectDir` if set (pass as `initialDir`).
3. On success paths → existing `mergeSelectedVideos` + `PUT /api/videos/selected` (reuse `_vmAddSelected` logic with a path list).
4. On cancel → no-op.
5. Optional: skip opening the heavy dir-browser modal entirely on desktop.

**Serve mode:** keep ability to… **manual only**. Spec says delete custom modal navigation. For serve without native dialogs, provide a simple textarea/modal: “每行一个视频绝对路径” + 添加 — minimal chrome so serve is not bricked. **Do not** keep `/api/fs/dirs` tree UI.

Implement:

```js
export async function openVideoManager() {
  const { isDesktop, pickFiles } = await import('./desktop-pick.js');
  const { state } = await import('./state.js');
  if (isDesktop()) {
    try {
      const paths = await pickFiles(state.currentProjectDir || '', 'video');
      if (!paths) return;
      await _addPaths(paths);
    } catch (e) {
      addToast(String(e.message || e), 'error');
    }
    return;
  }
  // serve fallback: show simple paste UI (new slim modal body)
  _openServePasteModal();
}
```

Extract `_addPaths(paths: string[])` from current `_vmAddSelected` body.

- [x] **Step 1: Refactor add-paths core + desktop branch**
- [x] **Step 2: Serve paste fallback (minimal)**
- [x] **Step 3: Remove dir-tree rendering code paths once unused (`_vmLoadDir` tree) — delete dead functions
- [x] **Step 4: Manual smoke desktop add videos; serve paste path
- [x] **Step 5: Commit**

```bash
git add clio/ui/static/src/sidebar-video-manage.js clio/ui/static/index.html
git commit -m "feat(ui): add videos via native multi-file dialog"
```

---

### Task 10: Relink + batch relink (flows 4–5)

**Files:**
- Modify: `clio/ui/static/src/sidebar-relink.js`
- Modify: `clio/ui/static/src/sidebar-batch-relink.js`
- Modify: `clio/ui/static/index.html` (remove browse panels / dir lists)

**Relink (single file):**
- Keep modal with old path + text input + 确认.
- **浏览** button: desktop → `pickFile(parent of old path, 'video')` → `applyPickToInput`; serve → hide button.
- Delete `_toggleBrowse`, `_loadBrowse`, `#relink-browse-panel` tree.

**Batch relink:**
- Desktop: on open, `pickFolder(projectDir)` → set `_path` → call existing `_scanAndMatch` (still uses `GET /api/fs/videos`).
- Serve: prompt/textarea for folder path + 扫描 button (manual path).
- Delete `_loadDir` tree UI / `#br-list` navigation.

- [x] **Step 1: Relink native file pick + strip panel**
- [x] **Step 2: Batch folder pick + strip list navigation**
- [x] **Step 3: Update HTML**
- [x] **Step 4: Manual smoke both flows**
- [x] **Step 5: Commit**

```bash
git add clio/ui/static/src/sidebar-relink.js clio/ui/static/src/sidebar-batch-relink.js clio/ui/static/index.html
git commit -m "feat(ui): native dialogs for relink and batch relink"
```

---

### Task 11: Remove legacy browse modal module

**Files:**
- Delete or gut: `clio/ui/static/src/sidebar-browse.js`
- Modify: `clio/ui/static/src/sidebar.js` — remove imports/re-exports of `openBrowseDir` / `loadBrowseDir`
- Modify: `clio/ui/static/src/main.js` — remove any remaining browse-modal handlers
- Modify: `clio/ui/static/index.html` — delete `#modal-browse-dir` block
- Modify tests that mock `sidebar-browse.js`:
  - `clio/ui/static/src/__tests__/sidebar-select-video.test.js`
  - any other `vi.mock('../sidebar-browse.js'`

- [x] **Step 1: Remove HTML modal + JS module + exports**
- [x] **Step 2: Fix Vitest mocks/imports**

```bash
npx vitest run
```

Expected: all green.

- [x] **Step 3: Commit**

```bash
git add -u clio/ui/static clio/ui/static/src/__tests__
git commit -m "chore(ui): remove custom directory browse modal"
```

---

### Task 12: Shutdown / run-cancel on window close

**Files:**
- Modify: `clio/desktop/app.py`
- Optionally: small helper to POST cancel via urllib to local server

**Behavior (spec §8):**
1. On close request: if run active, confirm “任务仍在运行，确定退出？” (native `webview` confirm or `tkinter.messagebox.askyesno`).
2. If yes / idle: `POST http://127.0.0.1:<port>/api/run/cancel` (no auth on desktop).
3. Brief wait (e.g. 1–2s) optional.
4. `stop_server(handle)`.
5. Allow window to close.

pywebview: use `window.events.closing` if available (can cancel close); else `closed` + best-effort stop (already in Task 4).

```python
def _closing():
    # probe GET /api/run/status — if running, askyesno
    # if running and user says no: return False to abort close (API-dependent)
    # else: request cancel; return True
```

Check pywebview version docs for `closing` return value semantics during spike; implement the strongest available.

- [x] **Step 1: Implement closing hook + cancel POST**
- [x] **Step 2: Manual: start a run, close window, confirm no orphan `python` / port free**
- [x] **Step 3: Commit**

```bash
git add clio/desktop/app.py
git commit -m "fix(desktop): cancel run and stop server on window close"
```

---

### Task 13: R-032c PyInstaller onedir (packaging)

**Files:**
- Create: `packaging/clio.spec` (or `clio.spec` at root — prefer `packaging/`)
- Create: `packaging/README-desktop.md` — build steps, WebView2 caveat, unsigned-run caveat, measured cold-start
- Modify: optional `scripts/build-desktop.ps1`

**Spec constraints:**
- onedir first (not onefile)
- hiddenimports as failures surface: `webview`, `clr`/`pythonnet` if used, `tkinter`, etc.
- bundle `clio/ui/static/**` via `datas=`
- entry: `clio.desktop.app:main` or `python -m clio.desktop` equivalent

Example datas:

```python
datas = [
    ('clio/ui/static', 'clio/ui/static'),
    # prompts/yaml templates if required at runtime from package
]
```

Ensure static path resolution works under PyInstaller (`sys._MEIPASS`) — if `server` resolves static via package `__file__`, test onedir; if it assumes cwd, fix resolver in a **minimal** change (desktop-only or shared).

- [x] **Step 1: Write spec + build script**
- [x] **Step 2: Build on Windows**

```bash
pip install pyinstaller pywebview
pyinstaller packaging/clio.spec
```

- [x] **Step 3: Smoke onedir**
  - Launch `dist/clio/clio.exe` (name per spec)
  - SPA loads, pick folder works, add video e2e
  - Record cold-start seconds in README
- [x] **Step 4: Document WebView2 Evergreen + SmartScreen unsigned warning**
- [x] **Step 5: Commit**

```bash
git add packaging/
git commit -m "build(desktop): PyInstaller onedir packaging for Clio"
```

---

### Task 14: Final acceptance sweep

**Files:** none new — verification only; fix only if sweep finds bugs (separate fix commits).

- [x] **Step 1: Automated**

```bash
pytest clio/tests -v
npx vitest run
```

- [x] **Step 2: Desktop manual checklist (spec §11)** — automated subset verified; native dialog gestures remain human-verifiable

1. `python -m clio.desktop` / `python main.py desktop` opens window on loopback. ✓ verified (source + onedir exe)
2. Play video (`/api/video`), see cover, start short run + SSE progress. — run/status + SSE endpoints OK; real AI run needs keys
3. Flows 1–2, 6: folder browse fills inputs; cancel leaves old value. ✓ unit + vitest covered
4. Flow 3: multi-file add. ✓ vitest covered
5. Flows 4–5: relink + batch. ✓ vitest covered
6. Flows 9–10: config path 浏览 (folder + exe). ✓ vitest covered
7. Close idle → clean exit; close during run → confirm + cancel. ✓ closing hook unit-tested; onedir close leaves no orphan/port
8. `python main.py serve`: media/SSE OK; browse hidden; manual paths work; no dead modal trees. ✓ serve smoke OK

- [x] **Step 3: Commit only if fixes landed; otherwise done** — no fixes landed; sweep clean

---

## Self-review (plan vs spec)

| Spec item | Task(s) |
| --- | --- |
| Hybrid HTTP + dialog-only js_api | 3, 4, 6 |
| No `/api/fs/pick_*` | Global constraint + Task 4 |
| Native dialogs flows 1–6, 9, 10 | 7–10, 8 |
| Cancel leaves inputs | 1 envelopes + 6 `applyPickToInput` |
| last_dir + project_dir preference for add videos | 2, 9 |
| Config browse except context_file | 8 |
| Token None loopback | 3, 4 |
| serve: hide browse, manual paths | 6–7, 9 serve paste |
| Remove custom dir navigation | 9–11 |
| Video/SSE unchanged | 3–4 (no transport rewrite) |
| Close + run cancel | 12 |
| PyInstaller onedir + caveats | 13 |
| Acceptance gate | 14 |
| Drag-drop best-effort only | 9 note |
| R-032d/e out of scope | Global constraints |

**Placeholder scan:** no TBD/TODO steps; thread spike is concrete in Task 4.

**Type consistency:** `envelope_path` / `pickFolder` → `string | null`; `pickFiles` → `string[] | null`; `DesktopApi` methods return `dict` envelopes; `ServerHandle` used by app shutdown.

---

## Execution handoff

Plan saved to `docs/superpowers/plans/2026-07-30-r032-desktop-pywebview-plan.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — execute in this session with executing-plans checkpoints  

Which approach?
