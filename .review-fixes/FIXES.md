# clio 代码审查修复包（2026-11-22）

共 **140** 个 patch，一个 patch 对应一个 commit。提交顺序：G0 → G1 → G2 → G3 → G4 → G5 → G6，组内按编号。

## 使用方法（PowerShell，在项目根目录执行）

```powershell
# 1) 先提交你自己尚未提交的改动。patch 是基于当前工作区生成的，不要用 stash，否则 patch 可能打不上
git status

# 2) 只做检查，不改动任何文件（在临时 worktree 中按顺序 apply）
powershell -ExecutionPolicy Bypass -File .review-fixes\apply-fixes.ps1 -DryRun

# 3) 新建分支 fix/code-review-2026-11-22，逐个 apply 并 commit
powershell -ExecutionPolicy Bypass -File .review-fixes\apply-fixes.ps1

# 4) 运行测试
.venv\Scripts\python -m pytest -q
npm test
```

- 默认会跳过 git hooks（`--no-verify`）。原因：pre-commit 会自动格式化文件，格式化后后续 patch 可能打不上。所有 patch 提交完后，脚本会统一跑一次 `ruff format`，结果作为最后一个 commit。
- 如果某个 patch 失败，脚本会停下并提示对应文件。修好后用 `-From "G2\005_B-07.patch"` 从该 patch 继续。
- 整个分支确认无误后，可以删除 `.review-fixes\` 目录。

## 已验证 / 未验证

- ✅ 已从你机器上拉取原始文件，按顺序应用全部 patch，结果与修复后的代码逐字节一致；CRLF/LF 换行符保持原样。
- ✅ 所有改动过的 Python 文件都能通过 AST 语法解析。
- ⚠️ **pytest、vitest、ruff、mypy 都没有运行过**（沙箱里装不了），需要你在本地跑。

## 会改变现有行为的地方

| 改动 | 说明 |
|---|---|
| B-21 | 旅行汇总文件由 `trip_plan.json` 改名为 `_trip_summary.json`，避免覆盖某一天的计划（前端未引用旧文件名）|
| D-01/D-02 + E-11 | 本地模式也启用会话 token，通过 `clio_session` HttpOnly cookie 下发；跨站请求按 `Sec-Fetch-Site` 拒绝；媒体和 SSE 的 URL 不再带 `?token=[REDACTED_PARAM] 局域网模式下，访问首页时带了有效 token 才会下发 cookie |
| B-08 | 重剪失败或被取消时，旧剪辑恢复回原名，不再删除 |
| B-16 | 遵守服务端的 `Retry-After`；要求等待超过 60 秒的直接失败 |
| B-01 | 解码失败的波形任务记为 FAILED，并有 60 秒冷却，不再无限重启 |
| B-11 | 未读通知也会过期，期限为 max(3×保留天数, 90 天) |
| C-30 | pywebview 移到 `desktop` extra；只安装 wheel 的用户运行 `clio desktop` 需要 `pip install "clio[desktop]"` |
| A-14 | `serve` 新增 `--token-file` 和环境变量 `CLIO_API_TOKEN`（`--token` 仍可用）|
| D-09 | 保存全局配置改为与磁盘上的文件合并，不再整份覆盖（YAML 注释仍会丢失）|

## Commit 清单


### G0 · CI 恢复

- `001_C-01` — fix(encoding): strip UTF-8 BOM from plan_pacing sources
- `002_E-25` — chore(ui): strip UTF-8 BOM from frontend modules
- `003_C-02` — style(config): collapse triple blank lines so ruff format --check passes
- `004_C-22` — ci: run on pushes to branches containing a slash

### G1 · 核心模块 / ffmpeg

- `001_A-01` — fix(utils): stop closing atomic-write fd twice on replace failure
- `002_A-02` — fix(readiness): skip undecodable or non-object analysis JSON
- `003_A-03` — fix(readiness): tolerate ffprobe failures when collecting durations
- `004_A-04` — fix(readiness): keep collecting vmeta durations without ffprobe
- `005_A-06` — fix(io): accept UTF-8 BOM when reading plan and analysis JSON
- `006_A-07` — fix(utils): make run_probe cancellable and stop truncating JSON
- `007_A-08` — fix(whisper): bound pip install time and stream its output
- `008_A-09` — fix(analyze): guard trip-context cache with a lock
- `009_A-10` — fix(analyze): ignore CWD and tolerate non-UTF-8 trip_context.md
- `010_A-11` — fix(analyze): honour cancel in planning and unify cancel exception
- `011_A-12` — fix(analyze): retry plan draft once when the model returns non-JSON
- `012_A-13` — refactor(timecode): share one timestamp parser across analysis and plans
- `013_A-14` — feat(cli): read serve API token from file or CLIO_API_TOKEN
- `014_A-15` — perf(run-scope): list compressed media once per scope resolution
- `015_A-16` — perf(readiness): compute media index aliases once per cut/export
- `016_A-17` — fix(evaluation): match facts only against model-written content
- `017_A-18` — fix(ffmpeg): re-read checksums once on mismatch from mutable latest
- `018_C-24` — fix(ffmpeg): find package-manager installs, log failures, allow cancel
- `019_C-25` — fix(ffmpeg): pick the current release asset from the checksum list
- `020_A-19` — fix(ffmpeg): log swallowed binary lookup and verification errors
- `021_A-20` — fix(utils): keep Windows ffmpeg search roots off POSIX and the CWD
- `022_A-21` — refactor: drop unused prompt renderer and physical window slicing
- `023_A-23` — refactor: share cut/export plan checks and atomic state writes
- `024_A-24` — fix(cli): sanitize export day dir and catch unprepared command errors
- `025_A-25` — fix(plan): keep _schema_version and reject non-finite plan numbers
- `026_B-03b` — fix(vmeta): keep a separate .vindex per same-stem original

### G2 · tasks / asr / export / 任务中心

- `001_B-01` — fix(waveform): stop re-running ffmpeg after a decode failure
- `002_B-03` — fix(reindex): never overwrite foreign or stale .vmeta sidecars
- `003_B-02` — fix(task-center): only notify on failure for background tasks
- `004_B-04` — fix(migrate): keep projects.json intact when updating the registry
- `005_B-05` — fix(migrate): keep migration target inside the projects folder
- `006_B-06` — fix(cut,export): normalize plan segment fields before use
- `007_B-07` — fix(cut): refuse to overwrite a leftover .clio_bak on re-cut
- `008_B-08` — fix(cut): restore stale cut artifacts after a failed or cancelled re-cut
- `009_B-09` — perf(task-center): set WAL journal mode once per database
- `010_B-10` — perf(task-center): skip progress writes that change nothing
- `011_B-11` — fix(task-center): expire unread notifications so cleanup can progress
- `012_B-12` — perf(task-center): stop querying SQLite under the SSE wake-up lock
- `013_B-13` — fix(task-center): keep task finalization and recovery from crashing
- `014_B-14` — fix(waveform,transcribe): bound ffprobe/ffmpeg runs and honor cancel
- `015_B-15` — perf(waveform): compute peaks with array slices instead of a sample loop
- `016_B-16` — fix(asr): honor Retry-After instead of capping it at 3 seconds
- `017_B-17` — fix(asr): use a fresh OSS key when retrying an upload
- `018_B-18` — fix(asr): treat every non-running DashScope status as terminal
- `019_B-19` — fix(tasks): escape formula-like cells in summary.csv
- `020_B-20` — fix(analyze): match single-file compressed clips exactly
- `021_B-21` — fix(plan): stop the trip summary from overwriting a day plan
- `022_B-22` — fix(refine): do not overwrite edits saved while the LLM was refining
- `023_B-23` — fix(label): skip unreadable analysis JSON instead of aborting the batch
- `024_B-24` — fix(compress): write .vindex for finished files when a later one fails
- `025_B-26` — perf(reindex): parse videos.json once per run instead of per file
- `026_B-27` — refactor(tasks): share one renderer for analysis .txt files
- `027_B-28` — fix(asr): translate provider display names when they are read
- `028_B-29` — fix(task-center,waveform): bound per-key lock tables
- `029_A-06b` — fix(tasks): read user-edited JSON with utf-8-sig
- `030_A-21b` — chore(i18n): drop unused analyze.window_slice_* locale keys
- `031_B-03b` — fix(reindex): index every same-stem original separately

### G3 · AI / 配置 / 凭据 / 桌面 / 打包

- `001_C-03` — fix(credentials): keep keyring backend after a transient read failure
- `002_C-05` — fix(config): abort V1 migration when the backup cannot be written
- `003_C-06` — fix(config): read .env files with a UTF-8 BOM
- `004_C-09` — fix(ai): share the token usage lock between store instances
- `005_C-16` — fix(desktop): take over a stale lock whose port hosts another server
- `006_C-20` — fix(packaging): detect universal2 Python via sysconfig on macOS
- `007_C-04` — fix(credentials): log and report why the OS keyring is unavailable
- `008_C-07` — fix(config): ignore inline comments in .env values
- `009_C-08` — fix(config): treat empty YAML sections as empty mappings
- `010_C-10` — fix(ai): keep corrupt token usage files and backfill missing fields
- `011_C-11` — fix(ai): scope structured output fallback per model and schema
- `012_C-12` — fix(ai): report non-JSON replies and retry transient 4xx statuses
- `013_C-13` — fix(ai): let KeyboardInterrupt escape the Gemini retry wrapper
- `014_C-14` — fix(ai): stop retrying local file errors in the Gemini provider
- `015_C-17` — fix(scripts): use the default web port in serve.ps1
- `016_C-18` — fix(desktop): keep the window open when the quit dialog fails
- `017_C-19` — fix(desktop): deliver Windows toasts and guard notify-send arguments
- `018_C-21` — fix(packaging): require keyring in desktop builds
- `019_C-23` — chore(git): ignore local runtime and scratch files
- `020_C-26` — fix(credentials): ignore empty inline keys on shared credential refs
- `021_C-27` — fix(hooks): handle non-ASCII and renamed files in pre-commit
- `022_C-28` — refactor(credentials): share one masked-placeholder constant
- `023_C-29` — fix(config): raise ValueError for unsupported export.output_subdir
- `024_C-30` — build: move pywebview to an optional desktop extra
- `025_D-10` — feat(config): validate candidate project.yaml without writing it

### G4 · UI 后端

- `001_D-01` — fix(ui): reject cross-site API requests via Sec-Fetch-Site
- `002_D-03` — fix(transcripts): hold path lock across revision check and write
- `003_D-04` — fix(refine): avoid lost edits and duplicate runs in /api/refine
- `004_D-05` — fix(ui): resolve texts/scripts in configured artifact subdirs
- `005_D-09` — fix(config): merge global PUT into config.yaml instead of replacing it
- `006_C-06` — fix(env): read .env with utf-8-sig in /api/env
- `007_D-02` — fix(ui): always require a session token, delivered as HttpOnly cookie
- `008_D-06` — fix(file-service): keep primary file in place while writing .bak
- `009_D-07` — fix(file-service): prune rotated backups without glob patterns
- `010_D-08` — fix(video): clamp Range end past EOF instead of returning 416
- `011_D-10` — fix(config): serialize and roll back project.yaml writes atomically
- `012_D-11` — fix(projects): write project.yaml before project.json on create/add
- `013_D-12` — fix(ui): lock videos.json/project.json updates and whitelist fields
- `014_D-13` — fix(videos): only case-fold video path keys on Windows
- `015_D-14` — fix(router): percent-decode path params once in Router.dispatch
- `016_D-15` — fix(projects): accept dict registry entries when resuming last project
- `017_D-16` — fix(projects): resolve ?project=<name> only within allowed projects
- `018_D-17` — perf(config-cache): make ConfigCache a real LRU
- `019_D-18` — fix(ai): cap provider timeouts for interactive AI requests
- `020_D-19` — fix(fs): skip unreadable entries in /api/fs/videos
- `021_D-20` — fix(transcripts): validate initial segments on transcript create
- `022_D-21` — fix(plan): strip only the trailing _plan suffix from day labels
- `023_D-22` — refactor(ui): drop dead duplicates from server.py and projects.py
- `024_D-23` — fix(ui): accept bodyless POST and centralize body error replies
- `025_D-24` — fix(sse): treat write timeouts as client disconnects
- `026_E-04b` — feat(api): add atomic POST /api/videos/selected/remove
- `027_A-16b` — perf(routes): compute media index aliases once per readiness check

### G5 · UI 前端

- `001_E-01` — fix(toast): remove toasts when animationend never fires
- `002_E-02` — fix(transcript): track revision after segment edit or delete
- `003_E-03` — fix(editor): keep dirty flag when edits land during a save
- `004_E-04` — fix(sidebar): serialize video removals to avoid lost updates
- `005_E-05` — fix(config): escape YAML keys rendered in the settings form
- `006_E-06` — fix(rerun): cancel stale overlay hide timer on a new rerun
- `007_E-07` — fix(tasks): rebuild the task SSE stream after it closes
- `008_E-08` — perf(tasks): coalesce SSE re-renders and keep open filters intact
- `009_E-09` — perf(runner): coalesce run preview refreshes from list re-renders
- `010_E-10` — fix(ui): run modal cleanup when closing dialogs with Escape
- `011_E-11` — fix(ui): stop leaking the API token in media and SSE URLs
- `012_E-12` — fix(plan): URL-encode the day label in /api/plan requests
- `013_E-13` — fix(notifications): back off SSE reconnects and stop on 401
- `014_E-14` — fix(models): serialize legacy download polling and stop off-page
- `015_E-15` — fix(viewer): drop stale loadedmetadata handler on source switch
- `016_E-16` — fix(video-info): dedupe probes and close cards of replaced rows
- `017_E-17` — fix(project): report failures when removing a project
- `018_E-18` — fix(config): keep list fields as arrays when editing settings
- `019_E-19` — perf(logs): update live log counts and ring trim incrementally
- `020_E-20` — fix(ui): escape remaining raw interpolations in innerHTML templates
- `021_E-22` — fix(ui): add download icon and fix file_text icon name
- `022_E-23` — perf(credentials): share one removal observer across fields
- `023_E-24` — fix(ui): fix status timer, null cancel, stale detail error, row menu
- `024_E-04c` — fix(sidebar): remove videos via atomic server endpoint

### G6 · 收尾

- `001_A-06c` — fix(vmeta): accept a UTF-8 BOM in .vmeta and .vindex sidecars
- `002_A-06d` — fix(ui): read plan JSON with utf-8-sig in plan and export routes
- `003_D-10b` — fix(config): validate project.yaml in memory before writing it

## 未完成或部分完成（需要后续处理）

| 问题 | 状态 | 原因 / 剩余工作 |
|---|---|---|
| A-15 | partial | fixed the per-artifact compressed/ re-listing (O(N^2)). Not done: batching ffprobe/GPMF via parallel _probe_creation_time_texts (needs restructuring of _resolve_capture's ordered fallback chain, untestable here), and limiting the rglob fallback in _texts_dirs, which intentionally mirrors clio/ui/ser |
| A-18 | partial | done: one checksum refetch on mismatch (race with republished latest). Not done: pinning RELEASE_BASE to a dated autobuild tag with SHA256 values embedded in code / attestation check - needs real upstream hashes obtained and verified by a maintainer (no network verification possible here; inventing  |
| C-25 | partial | done: asset name resolved from the published checksum list (no 404 after upstream n9.x bump). Not done: pinning to a dated tag with SHA256 embedded in code or BtbN attestation verification - needs maintainer-verified upstream hashes; the 'Verified' wording in UI/locale strings (other group) should a |
| A-22 | deferred | splitting plan_daily_vlog (~335 lines, 3 closures) into stage functions is an M refactor that cannot be exercised here (no pytest); A-11/A-12 already fixed its concrete bugs. The 'call_review ignores fallback' point is not a real bug: the branch only runs when the original draft had no issues, so re |
| B-09 | partial | Only the safe step shipped (journal_mode=WAL once per DB identity). Thread-local long-lived connections deferred: the store must survive the DB file being replaced while alive (test_store_repairs_missing_event_table_on_existing_database) and open handles block file replace/delete on Windows; Threadi |
| B-10 | partial | Shipped the lossless part (identical progress updates are no longer persisted). A time-based throttle (>=250ms) and an in-memory counter for TaskProgressReporter.next() were not added: dropping distinct updates can lose the last state without a flush hook, and next() relies on the persisted counter  |
| B-22 | partial | Lost-update guard shipped (byte comparison before write). Not done: artifact_guard backup integration and the unused overwrite= parameter of run_refine_texts/run_refine_scripts (callers in ui/routes pass it; changing its meaning needs the route owner) |
| B-26 | partial | Only the reindex videos.json re-read was fixed. Deferred: per-segment iterdir of compressed/texts in cut and the O(N^2) re-parse in transcript_align.enrich_matching_analysis_files, which need an index object threaded through several call chains (effort M, no way to run the suite here) |
| B-27 | partial | Merged the duplicated analysis .txt renderers (fixes the lost _changelog). Deferred: transcript payload duplication in transcribe.py and porting _extract_audio onto run_ffmpeg (different progress/disk-space/cancel semantics, untestable here); scripts.py md rendering left as is because its title fall |
| B-28 | partial | Fixed import-time translation of ASR display names. Deferred: storing notification title_key+params and translating at render time needs a schema migration in task_center plus changes in ui/routes/notifications.py and the frontend (other groups); removing the 30 unreferenced locale keys deferred bec |
| C-09 | partial | In-process race fixed with utils.path_write_lock (shared per path). Cross-process (CLI + UI at once) still needs a file lock or a JSONL/SQLite store; left out as an M-size redesign. |
| C-16 | partial | Fixed part 1 (any HTTPError counted as alive). Part 2 (read_lock/write_lock race between two simultaneous launches) needs an OS-level lock (O_EXCL / named mutex / flock) acquired at startup in desktop/app.py plus a token handshake; M-size, deferred as untestable here. |
| C-15 | deferred | M: needs an in-flight upload map (Future/Event per path_key) plus reference counting before deleting replaced uploads in GeminiProvider; concurrency change that cannot be exercised without the real SDK, so no safe small step. |
| C-18 | partial | Fail-closed default done. Not done: replacing the Tk prompt with pywebview window.create_confirmation_dialog and limiting the Tk fallback to Windows (macOS needs Tk on the main thread); needs real pywebview event-thread testing, M-size. |
| C-21 | partial | keyring install + spec guard + ps1 venv done. Pinning pyinstaller/pyobjc versions not done: no known-good version recorded in the repo (no lock file), and a guessed pin could break the release build. |
| C-23 | partial | Patterns added. Not done (needs the real git index, unavailable here): `git ls-files -- .env "*.sqlite3" opencode_session_import.json` check / `git rm --cached`, rotating keys if .env was committed, and a pre-commit secret check; one-off files x.txt / pelican-bicycle.svg were not added as patterns. |
| C-28 | partial | Only the mask-constant dedupe in owned files. Not done: ui/services/ai_models_service.py copy and the duplicated .env regex in ui/routes/env_routes.py (not owned: needs <ui files>: import MASKED_PLACEHOLDERS / a shared assignment regex); AppConfig getter/setter dedupe and the validator-vs-factory vi |
| C-30 | partial | pywebview moved to the desktop extra. Not done: making keyring a default dependency (THIRD_PARTY.md is not in this copy, and test_licenses requires every runtime dep to be listed there) and replacing deprecated [tool.setuptools.data-files] with package resources (needs clio/resources.py + layout cha |
| D-16 | partial | Security part fixed (name lookup limited to registry + serve root, sibling scan removed). The suggested mtime-keyed cache of the allowlist/registry resolve() was not added: it is a pure perf change touching every request path and cannot be exercised here without running the suite. |
| D-17 | partial | Only the ConfigCache LRU/lock part is fixed. Not done: deepcopy-free read-only AppConfig (callers may mutate the copy today; needs an audit of all call sites) and a TTL/incremental fingerprint for /api/videos (cache-correctness trade-off for offline NAS media); both need profiling and the test suite |
| D-18 | partial | Only the timeout caps for /api/ai/models and /api/ai/test drafts are done. Moving /api/refine and WNet network enumeration to TaskManager background jobs (or a separate slow-request budget in http_server) changes the API contract with the frontend (polling) and is an effort-M redesign; deferred. |
| D-22 | partial | Removed only dead/duplicate definitions (server.py locks + _setting_int, unrouted handle_post_project_select). Not done: routes/_common.py extraction of _managed_task_manager/_send_artifact_conflict/_format_bytes, explicit handler registry replacing server.py's noqa F401 re-exports (tests patch clio |
| E-21 | deferred | Effort L: introducing tHtml() and migrating hundreds of innerHTML t(...) call sites is a cross-module refactor that cannot be verified without running the suite; concrete injection points were fixed in E-05 and E-20 instead. |

另外需要你手动处理：执行 `git ls-files -- .env '*.sqlite3' opencode_session_import.json`，确认这些文件没有被 git 跟踪。如果 `.env` 曾经被提交过，请轮换其中的密钥（C-23）。
