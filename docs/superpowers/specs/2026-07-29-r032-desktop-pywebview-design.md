# Design: R-032 Desktop app + native file/folder dialogs (pywebview)

**Date**: 2026-07-29  
**Status**: Implemented ✅ — landed via `2026-07-30-r032-desktop-pywebview-plan.md`; refined by R-039 (single-instance + coexistence) and R-040 (out-of-box gaps); see `clio/desktop/*` and `packaging/` (P2-P33 sync)  
**Scope**: Ship Clio as a double-click Windows desktop app; replace custom in-browser directory/file pickers with native OS dialogs; keep the existing localhost HTTP transport for API, media, SSE, and static assets; polish config-page path inputs.  
**Approach**: **Hybrid (ROADMAP Option A)** — pywebview (system WebView2) hosts the existing SPA over `http://127.0.0.1:<port>`; existing `ThreadingHTTPServer` + handlers stay; native dialogs only via a thin `js_api` (`pick_*`); PyInstaller onedir for packaging.  
**Related**: R-032a–e (`ROADMAP.md:151`); R-038 done; config path inputs (`editor-config.js:185`); custom browse modal (`sidebar-browse.js`); video player (`viewer.js:140`); run SSE (`runner.js:351`, `routes/run.py:84`).

### Revision note (2026-07-29)

An earlier draft proposed retiring HTTP in favor of a full pywebview `js_api` mirror of ~70 routes. Review found that approach incompatible with:

- binary media (`GET /api/video` Range streaming into `<video src>`);
- cover images (`GET /api/cover` into `<img>`);
- run progress SSE (`GET /api/run/stream` via `EventSource`);
- ES-module loading under WebView2 (`file://` is unreliable);
- handler shape (handlers write HTTP responses via `_send_json` / `_send_bytes` / `wfile`, they do not return dicts).

This revision **aligns with ROADMAP’s default lean** (`ROADMAP.md:170`): thin desktop host + auto-start local server. Full HTTP→bridge migration is explicitly out of scope (future optional epic).

---

## 1. Goals and non-goals

### Goals

1. Ship Clio as a **single double-clickable Windows onedir** — no Python install required for end users, no terminal, no external browser tab.
2. Every place the user currently picks a file or folder uses a **native OS dialog** that returns a real **absolute path** (the thing pure browser APIs cannot provide).
3. Desktop shell is a **thin host**: start localhost server → open pywebview to that origin → expose **dialog-only** Python helpers. Business logic, routes, and validation stay in existing `clio/ui/**` and `clio/`.
4. Config-page path fields gain a **浏览** button and **last-used directory memory**.
5. Existing `clio/` package, CLI (`main.py analyze|plan|…`), `python main.py serve`, and unit tests are **not rewritten** — desktop is an additional entrypoint.

### Non-goals

- macOS/Linux parity in v1 (Windows-first; design must not block later ports).
- Code signing / InnoSetup installer (document unsigned-run caveats; defer R-032e).
- Auto-bundle or download ffmpeg/Whisper (R-032e / R-028; this round only **discovers** via existing deps UI).
- System tray, first-run wizard, open-data-folder buttons (R-032d).
- Rewriting the frontend in React/Qt/native widgets.
- Silent auto-update channel.
- **Retiring the stdlib HTTP server or replacing `fetch` / `EventSource` / `<video src>` with a full `js_api` route mirror** (explicitly deferred; see §4.7).
- Changing AI / plan / cut / export business logic.
- Single-instance enforcement (defer R-032d; v1 may open a second window).

### Success criteria

- `python -m clio.desktop` (and `python main.py desktop`) starts a localhost server, opens a native window on that origin, and closing the window stops the server and exits cleanly (see §8 close policy).
- Flows 1–6, 9, 10 (see §3) use native OS dialogs; custom HTML picker UIs for those flows are removed or reduced to non-browse chrome.
- Native dialogs return absolute paths that pass existing backend validation unchanged.
- Config path fields (`paths.*` except skipped keys, `export.jianying_draft_dir`, `script.template_file`) have working **浏览** buttons; last-used directory is recalled (see §6.3). `context_file` remains text-only (no browse) as today.
- Video playback (`/api/video`), covers (`/api/cover`), waveform JSON, and run SSE continue to work under the desktop window without transport rewrite.
- `python main.py serve` still works (API/media/SSE unchanged; path pickers become manual-text with browse hidden per §4.4).
- `clio/` unit tests green without forced handler signature edits; new `desktop/` code has tests; Vitest suite green for both browse-path changes and unchanged `api()` behavior.
- PyInstaller onedir build launches on a clean Windows machine; cold-start is **measured and documented** (target ≤ 5s warm SSD; not a hard ship gate if lazy-import work is tracked).

---

## 2. Background

| Fact | Detail |
| --- | --- |
| Current entry | `python main.py serve` → `clio.ui.server.run` (`clio/ui/server.py:536`); stdlib `ThreadingHTTPServer` on `127.0.0.1`; static ES modules under `clio/ui/static/` |
| Routing | `Router` + `_resolve_handler` (`server.py:441`); ~70 routes (`server.py:442-516`) |
| Handler shape | Handlers take `HandlerProtocol` and **write HTTP** (`_send_json`, `_send_bytes`, `_send_video_range`, `send_error`, SSE `wfile`) — they do **not** return response dicts |
| Media | `GET /api/video` → `handle_get_video` → `_send_video_range` (Range); frontend sets `player.src` (`viewer.js:140`). `GET /api/cover` → raw image bytes (`routes/texts.py:37`) |
| SSE | `GET /api/run/stream` (`routes/run.py:84`); frontend `EventSource` (`runner.js:351`) |
| FS sandbox | `_is_allowed_path` (`routes/fs.py:20`); dirs/videos/mkdir/reveal endpoints |
| Path validation | `_is_safe_basename`; selected-videos allowlist; relink resolve+ext; project `is_dir()` checks |
| Frontend transport | `static/src/api.js` — `api(method, path, body)` → `fetch`; separate `EventSource` / media URLs bypass `api()` |
| Custom pickers | `sidebar-browse.js`; video manage / relink / batch-relink; global `.browse-btn` → `openBrowseDir` (`main.js:300`) |
| Drag-drop | Video manager only; non-standard `File.path` (Chrome/Edge Windows) |
| Config path fields | Plain text inputs (`editor-config.js:185-203`); `context_file` skipped; no browse buttons |
| Roadmap default | Option A: pywebview host → auto-start local server on `127.0.0.1` → open WebView; PyInstaller onedir (`ROADMAP.md:170`) |

### Why native dialogs (not why kill HTTP)

The browser does not expose on-disk absolute paths for `<input type="file">` / `showDirectoryPicker()`. Clio stores and validates real path strings (`Path.resolve()`, allowlists). A Python-side OS dialog returns the path string the rest of the stack already expects. **HTTP remains the right transport** for JSON APIs, Range video, images, and SSE.

---

## 3. Inventory: every file/folder touch point (target → native dialog)

| # | Flow | Today | Files | Target |
| --- | --- | --- | --- | --- |
| 1 | New project: project dir + output dir | custom dir modal + manual text | `index.html`; `main.js`; `sidebar-browse.js` | native **folder** dialog |
| 2 | Open project by path | manual text + browse + list | `index.html`; `main.js` | native **folder** dialog (path input remains) |
| 3 | Add videos | custom dir browser + checkboxes + drag-drop | `sidebar-video-manage.js` | native **multi-file** dialog (video filter); keep drag-drop as best-effort secondary |
| 4 | Relink one offline video | manual text + file browse | `sidebar-relink.js` | native **single-file** dialog (video filter) |
| 5 | Batch relink | custom dir browser → scan | `sidebar-batch-relink.js` | native **folder** dialog → existing server-side scan |
| 6 | Cut output dir | manual text + `.browse-btn` | `editor-plan.js`; `main.js` | native **folder** dialog |
| 7 | Cut "open dir" | OS reveal | `editor-plan.js`; `fs.py` | unchanged |
| 8 | Reveal project dir | OS reveal | `main.js`; `fs.py` | unchanged |
| 9 | JianYing export dest | config text only | `editor-config.js`; export routes | native **folder** on config field |
| 10 | Config path fields | manual text, no browse | `editor-config.js:185` | per-field **浏览** + last-used dir (`context_file` excluded) |

After this work: flows 1–6, 9, 10 use native dialogs; 7–8 stay reveal. Custom HTML navigation UIs (`#modal-browse-dir`, browse panels inside relink / video-manage / batch-relink) are removed or reduced to confirm/review chrome only.

---

## 4. Architecture

### 4.1 Process model (Hybrid)

Single OS process, two cooperative pieces:

1. **Main thread**: pywebview window loop (required by several backends).
2. **Background thread**: existing `ThreadingHTTPServer` bound to `127.0.0.1` on an **ephemeral or fixed free port** chosen at startup.

```text
┌──────────────────────────────────────────────────────────┐
│  pywebview (WebView2)                                    │
│    location = http://127.0.0.1:<port>/                   │
│    fetch / EventSource / <video src> / <img src>  ───────┼──► HTTP (unchanged routes)
│    window.pywebview.api.pick_folder|pick_files|…  ───────┼──► dialogs only
└──────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────┐
│  clio.ui.server (ThreadingHTTPServer)                    │
│  static + all /api/* including video Range + SSE         │
│  (no /api/fs/pick_* — dialogs only via js_api)           │
└──────────────────────────────────────────────────────────┘
```

Desktop 启动的 server 仍在 `127.0.0.1`；桌面窗口与 server 同进程，按 §6.6 禁用 token（不弹 token 界面，非对外裸奔——server 仅绑 loopback）。serve mode 保留既有 token 行为。

### 4.2 Why HTTP stays (decision record)

| Concern | Full `js_api` mirror | Hybrid (this spec) |
| --- | --- | --- |
| `<video src>` + HTTP Range | Broken / needs custom protocol | Works today |
| `<img>` covers | Needs blob/data URL plumbing | Works today |
| `EventSource` run progress | No streaming equivalent | Works today |
| ES modules in WebView2 | `file://` often fails | `http://127.0.0.1` works |
| Handler reuse | Requires response-capture refactor | True reuse |
| Native absolute paths | Dialogs via Python | Same |
| Scope / risk | Large transport rewrite | Thin shell + picker swap |

**Decision:** Hybrid for R-032a/b/c. Full bridge is a separate future epic if ever justified (e.g. stricter isolation); not required for product goals 1–2–4.

### 4.3 Native dialog wrapper

```python
# clio/desktop/dialogs.py (new)
def pick_folder(initial_dir: str | None = None) -> str | None: ...
def pick_files(
    initial_dir: str | None = None,
    multiple: bool = True,
    exts: list[str] | None = None,
) -> list[str]: ...
def pick_file(
    initial_dir: str | None = None,
    exts: list[str] | None = None,
) -> str | None: ...
# reveal stays on existing POST /api/fs/reveal (or thin wrapper)
```

- **Windows impl**: prefer `tkinter.filedialog` (`askdirectory`, `askopenfilenames`, `askopenfilename`) with `filetypes` from `clio._constants.VIDEO_EXTENSIONS` (and `*.exe` for ffmpeg/ffprobe). Tk ships with CPython.
- **Threading**: Tk dialogs typically need the main thread; pywebview may invoke `js_api` on a worker. **Spike early in R-032b**: (1) main-thread dispatch into the webview loop, or (2) Win32 `IFileOpenDialog` via COM from a worker after `CoInitialize`. Choose the option with fewer PyInstaller hidden-imports. Document the choice in the plan after the spike.
- **Return contract** (must be stable for JS):
  - Success folder/file: absolute path string(s).
  - Cancel: `None` / empty list — **frontend must not clear or overwrite the input**.
  - `js_api` return envelope (recommended):

```json
{ "ok": true, "path": "D:\\trip\\2026-07" }
{ "ok": true, "paths": ["D:\\a.mp4", "D:\\b.mp4"] }
{ "ok": false, "cancelled": true }
{ "ok": false, "error": "..." }
```

- `initial_dir`: last-used memory (§6.3), else sensible flow default (e.g. current `project_dir` when adding videos if known), else `Path.home()`.

### 4.4 Exposing dialogs to the frontend

**Locked: `js_api` only for `pick_*`** — the single desktop-only surface. No new HTTP routes for picking.

| Why this shape | Detail |
| --- | --- |
| No new routes | Clear desktop-only surface; no fork in `api()` for pickers |
| Threading is the real risk, and HTTP routes don't dodge it | Calling a native OS dialog from the HTTP server thread is *the same* GUI-on-background-thread problem as calling it from the `js_api` worker — Tk wants the main thread; Win32 COM needs `CoInitialize` on the calling thread. A `/api/fs/pick_*` route is not a safe fallback for `js_api` threading pain; it just moves the problem. Solve the thread model once, inside `js_api` (main-thread dispatch or COM-initialized worker), per the §8 spike. |

Frontend helper (e.g. `pickFolder(initialDir)` in `api.js` or `desktop-bridge.js`):

- If `window.pywebview?.api?.pick_folder` (or agreed names) exists → call it and apply the envelope in §4.3.
- Else → **no native dialog** (browser/serve mode).

**Serve vs desktop product rule (locked):**

| Mode | Picker behavior |
| --- | --- |
| Desktop (`pywebview` present) | Native dialogs for flows 1–6, 9, 10 |
| `python main.py serve` + browser | **Manual path text only** for those flows; **delete** custom modal HTML/JS navigation; browse buttons are **hidden** (preferred) or show a one-line toast “请手动粘贴路径；桌面版支持系统对话框” — pick **hide** in implementation unless a visible affordance is needed for discoverability |

Do **not** maintain two full picker UIs. Do **not** add `/api/fs/pick_*` HTTP routes (would invite calling OS GUI dialogs from the server thread for external browser tabs — wrong process model and solves nothing).

### 4.5 Frontend changes (pickers only; transport mostly unchanged)

- **Do not** rewrite `api.js` to route all calls through `pywebview.api`.
- Add a small desktop pick helper used by:
  - `.browse-btn` delegation (`main.js:300`) — replace `openBrowseDir(data-target)` with native folder pick → write path into target input on success only.
  - Config form renderer — add 浏览 buttons next to path fields.
  - Video manage / relink / batch-relink — replace dir-browser columns with “选择…” that opens native dialogs; keep confirm/list UI where still useful (e.g. multi-select review can be the OS multi-file dialog itself).
- Media URLs, SSE, and `api()` JSON calls stay as today (relative to `http://127.0.0.1:<port>`).

### 4.6 Entry point

```python
# clio/desktop/app.py
def main() -> int:
    cfg = load_config()
    host, port = start_server_thread(cfg)   # reuses server factory; 127.0.0.1; free port
    url = f"http://{host}:{port}/"
    window = webview.create_window("Clio", url, js_api=DesktopApi(), ...)
    webview.start()
    stop_server()
    return 0
```

- `python -m clio.desktop` and `python main.py desktop` both call `main()`.
- Static assets continue to be served by the existing static handlers (dev tree or PyInstaller bundle path via existing static_dir resolution — extend if `_MEIPASS` needed).
- **Never** load the SPA primarily via `file://` for production desktop.

### 4.7 Out of scope transport epic (explicit)

Not in R-032a/b/c:

- Generic `invoke(method, path, body)` bridge replacing `fetch`.
- Per-route `config_get` / `project_create` adapters.
- Replacing SSE with polling-only (unless a future desktop-only optimization).
- Custom media URL scheme.

---

## 5. Phasing (maps to R-032a–e)

| Phase | ID | In this spec? | Deliverable |
| --- | --- | --- | --- |
| 0 | R-032a | ✅ | This design doc |
| 1 | R-032b | ✅ | `python -m clio.desktop`: server thread + window + native dialogs on flows 1–6, 9, 10; config browse + last-used; serve mode still works |
| 2 | R-032c | ✅ | PyInstaller onedir; hiddenimports; cold-start notes; WebView2 + unsigned-run caveats |
| 3 | R-032d | ❌ | tray, first-run wizard, open data/log folders, single-instance |
| 4 | R-032e | ❌ | bundle ffmpeg, code-sign, installer |

**R-032b implementation order (required):**

1. Spike: window + HTTP URL loads SPA (modules work).
2. Spike: one native `pick_folder` from JS (thread model decided).
3. Wire all §3 picker flows + config buttons.
4. Close/shutdown + logging.
5. Tests + manual smoke.

R-032c only after b smoke is green.

---

## 6. UX

### 6.1 Browse buttons replace custom modal navigation

- Existing `.browse-btn` keeps the button chrome; handler opens native folder dialog.
- Config path fields gain **浏览** beside the input in `_renderConfigForm`.
- `paths.ffmpeg` / `paths.ffprobe`: **single-file** dialog, filter `*.exe` (plus “all files”).
- Folder-valued settings: folder dialog.
- `script.template_file`: single-file dialog (reasonable document/text filters).
- `context_file`: **no** browse button (unchanged skip).

### 6.2 Manual entry remains

All path inputs stay editable. Dialog fills on success only; cancel leaves prior value. Paste/edit always allowed.

### 6.3 Last-used directory memory

- Persist parent of last successful pick to `{config_dir}/desktop-state.json` (or agreed config-adjacent path).
- Schema (v1): `{ "last_dir": "<abs>" }` — single global key.
- Optional later: per-flow keys; v1 global is enough.
- When opening “add videos”, if `state.currentProjectDir` is set, prefer that as `initial_dir` over global last_dir when it exists on disk (small UX win; document in plan).

### 6.4 Drag-drop

- Keep as secondary on video manager.
- **Risk:** WebView2 may not expose `File.path`. Treat as best-effort; if paths are empty, toast explaining to use the native multi-file button. Do not block R-032 on drag-drop parity.

### 6.5 Removal of custom modal navigation

- `#modal-browse-dir` + `sidebar-browse.js` navigation: **delete** once desktop helper is wired (§4.4).
- Relink / video-manage / batch-relink: remove in-modal directory trees; desktop uses native dialogs; serve uses manual paths / non-browse chrome; keep post-pick review UI only where it still adds value (e.g. batch match results after a path is known).

### 6.6 Auth / token under desktop

- Desktop local window: **no auth modal** on startup; do not require `api_token` for loopback desktop sessions.
- **已确认的 server 语义**：`clio.ui.server.run` 对 `api_token=None` 且 host 为 loopback（`127.0.0.1`/`localhost`/`""`）时，`TOKEN = ""`（跳过鉴权，见 `server.py:554-558`）——即"无 token"而非"生成临时 token"。Desktop 直接传 `api_token=None` 即可，无需改 server 默认行为，也无需进程内注入。
- `viewer.js` / `runner.js` 已在 token 为空时跳过 token query 参数 —— 桌面窗口无 token 时这些 URL 不带 `?token=`，server 照常放行（loopback 无鉴权）。
- `python main.py serve` keeps existing token behavior for power users / LAN experiments（host 非 loopback 时 server 会生成 token）。
- Plan 中注明 desktop 启动时显式传 `api_token=None`（而非依赖默认），并复用 loopback 无鉴权分支。

### 6.7 ffmpeg / Whisper discovery (no install)

- On desktop start, existing deps endpoints / UI continue to report ffmpeg presence.
- Missing tools: same UI affordances as serve mode; no silent download in this spec.

---

## 7. Backend and validation (unchanged)

- Path validation, allowlists, atomic writes: **verbatim**.
- `GET /api/fs/dirs` and `GET /api/fs/videos`: no longer required for picker **navigation**; may remain for any leftover list UX or tests until unused, then optional cleanup (not required for acceptance).
- `POST /api/fs/mkdir`: optional; OS dialog can create folders. Keep endpoint; no need to feature it in new UI.
- `POST /api/fs/reveal`: unchanged.
- No new FS permission model; OS dialog is the user gate; existing sandbox remains defense-in-depth for HTTP FS APIs.

---

## 8. Risks and open questions

| Risk / question | Mitigation / decision |
| --- | --- |
| **Tk vs pywebview main-thread** | Early R-032b spike; fallback Win32 `IFileOpenDialog`; record winner in plan |
| **WebView2 missing (old Win10)** | Document Evergreen Runtime requirement; optional bootstrapper note in R-032c |
| **ES modules / wrong origin** | Always http://127.0.0.1 — never ship file:// as primary |
| **PyInstaller hiddenimports** | onedir first; pin `clio.spec`; add imports as failures surface |
| **Cold start** | Measure; lazy-import heavy stacks from desktop entry; budget is target not hard fail |
| **AV false positives** | Document; code-sign in R-032e |
| **No console after GUI start** | Log to config_dir logs (reuse session log patterns); errors via toast where possible |
| **File.path drag-drop in WebView2** | Best-effort; native multi-file is primary (§6.4) |
| **Close while run pipeline active** | On window close: request run cancel if running; join worker with timeout; then stop HTTP server; force-exit only after timeout. Confirm dialog optional v1 (“任务仍在运行，确定退出？”) — **include confirm if cancel is non-trivial** |
| **Port already in use** | Bind port 0 / search free port; pass chosen port into window URL |
| **Second instance** | Allowed in v1; document |
| **Serve mode after modal removal** | Hide browse buttons; manual path still works (§4.4 locked rule) |

---

## 9. Testing

- **Unit (Python)**: `dialogs.py` normalization + cancel/`None` behavior with mocked OS dialog; desktop-state last_dir read/write; server start helper picks a free port (if extracted).
- **Unit (JS)**: browse helper writes path only when `ok` and not cancelled; config form renders 浏览 for path keys and skips `context_file`; existing Vitest green.
- **Manual smoke (R-032b)**: `python -m clio.desktop` — SPA loads; play a video; start a short run and see SSE progress; exercise flows 1–6, 9, 10; cancel dialog leaves field unchanged; close during idle exits clean; close during run cancels/stops without orphan Python process.
- **Serve regression**: `python main.py serve` — JSON APIs + media + SSE still work; path flows usable per §4.4 rule.
- **Build smoke (R-032c)**: clean Windows machine / VM without dev Python; onedir launches; create project + add video e2e; record cold-start.
- **Regression**: `clio/` tests unchanged in intent; no mass handler signature rewrite.

---

## 10. Out of scope (explicit)

- R-032d tray / wizard / open-data-folder / single-instance.
- R-032e ffmpeg bundle / code-sign / InnoSetup.
- macOS/Linux packaging.
- Full HTTP→`js_api` transport migration.
- Frontend framework rewrite.
- Removing `python main.py serve`.
- AI/plan/cut/export logic changes.
- Auto-update.

---

## 11. Acceptance (implementation plan gate)

1. `python -m clio.desktop` opens a native window on `http://127.0.0.1:<port>/`; modules load; closing exits with server stopped (and run cancelled if needed).
2. Flows 1–6, 9, 10 use native OS dialogs; no custom HTML **directory navigation** remains for those flows.
3. Dialog success → absolute paths pass existing validation; cancel → inputs unchanged.
4. Config path fields (except `context_file`) have working 浏览 + last-used (and project_dir preference where specified).
5. `/api/video` playback, `/api/cover`, and `/api/run/stream` SSE work inside the desktop window.
6. `python main.py serve` still works for API/media/SSE; path flows work via manual text; browse buttons hidden (no dead controls, no dual picker UI).
7. `clio/` tests green; new desktop tests exist; Vitest green.
8. R-032c: onedir build documented; WebView2 + unsigned-run caveats; cold-start measured.

---

## 12. Implementation plan inputs (for writing-plans)

Suggested work packages (not a full plan):

1. `clio/desktop/` package: `app.py`, `dialogs.py`, `state.py` (last_dir), optional `api.py` (`DesktopApi` js_api).
2. Server launch helper extractable from `clio.ui.server.run` (start/stop without opening a browser).
3. Frontend: desktop pick helper; main browse delegation; config buttons; strip modal navigation from video/relink/batch flows.
4. Shutdown / cancel policy.
5. PyInstaller spec + docs (R-032c).
6. Tests + smoke checklist from §9.
