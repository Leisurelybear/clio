# Gotchas — Known Pitfalls & Lessons Learned

> Extracted from AGENTS.md §9. Reference document — load on demand when debugging or modifying affected modules.

### 9.1 Commas in ffmpeg Filter Expressions

In `scale=min(640,iw):-2`, the `,` is parsed by ffmpeg as a filter chain separator.
**Must** be written as `scale=min(640\,iw):-2` (in Python source: `\\,`).
See `clio/compress.py`.

### 9.2 Windows + `git filter-branch --msg-filter`

- When calling git via `cmd /c`, backslashes in paths are eaten by the shell as escape characters → always use forward slashes
- Chinese Windows' `sys.stdin.encoding` defaults to **GBK**, UTF-8 input gets decoded to `?` → Python filter scripts **must** use `sys.stdin.buffer.read()` for byte-level matching, **do not** use `sys.stdin.read()` text mode
- When PowerShell calls a bat file, paths containing spaces in parameters like `%1` get split → use `%~nx1` in the bat file to extract just the filename for comparison

### 9.3 Gemini File API

- After upload, file status is `PROCESSING`; must poll until `ACTIVE` before calling generate_content
- Poll interval is in `ai.providers.gemini.poll_interval_sec` (default 5 seconds)
- Access from mainland China requires a SOCKS5 proxy; use google-genai's `HttpOptions(transport=httpx.HTTPTransport(proxy=...))`

### 9.4 DeepSeek Model Names

- Standard models: `deepseek-chat` (V3), `deepseek-reasoner` (R1)
- Third-party API gateways may support custom names like `deepseek-v4-flash`; just fill in the actual available name
- If you encounter `404` or `model not found`, first fall back to the official standard model name for verification

### 9.5 `api_key_env` Field

- This is the **environment variable name** (e.g. `DEEPSEEK_API_KEY`), not the key itself
- Old code incorrectly filled the actual key into the `api_key_env` field, causing error messages to leak the key
- Fix: `clio/utils.py:mask_if_looks_like_key()` detects `sk-` / `AIza` prefixes and masks them

### 9.6 Misleading Name `analyze.skip_existing`

- Although the field is named `skip_existing`, it is actually a **skip toggle shared by all steps**
- Do not create a new `scripts.skip_existing` — reuse this one

### 9.7 Gemini Files API Upload Not Cleaned Up

- In `gemini.py`, `ensure_file_active` uploads video to File API, but **does not** request file deletion after the call completes
- Multiple analyze runs accumulate many files, eventually exhausting quotas (per-minute/daily upload limits)
- Fix: call `client.files.delete(name=file.name)` in a `finally` block to ensure cleanup
- Note: `with_retry` should not wrap the upload operation (otherwise retries re-upload), only wrap `wait` and `generate_content` after successful upload

### 9.8 Temporary File Residue

- Multiple places in the project use `NamedTemporaryFile(delete=False)` and forget to call `os.unlink`
- `.tmp` files are not automatically cleaned up on `interrupt` / `KeyboardInterrupt`
- Investigation: check temporary file usage in `server.py` / `utils.py`, prefer `delete=True` or `try/finally`

### 9.9 Function Side Effects

- Functions like `analyze_video` / `generate_voiceover` modify fields of the input dict (e.g. adding `_file_path` markers)
- If the caller reuses the same dict, unexpected side effects occur
- Fix: apply `copy.deepcopy()` to input parameters before modification

### 9.10 Cross-Platform File Ordering Inconsistency

- `Path.iterdir()` on Windows follows filesystem order (approximate creation time), on Linux the order is not guaranteed
- This causes `index` assignment to differ across systems
- Fix: always use `sorted(Path.iterdir())` to ensure consistent ordering

### 9.11 Pre-commit Hook

- The project provides a Python script at `.githooks/pre-commit` that auto-runs `ruff format` on staged `.py` files and only re-stages files that ruff actually modified (via `git diff --name-only`)
- This prevents unintended staging of workspace changes the user didn't `git add`
- `setup.ps1` auto-sets `git config core.hooksPath .githooks`
- Manual config: `git config core.hooksPath .githooks`
- The hook depends on `ruff` in `.venv`; if not found, it silently skips (does not block the commit)

### 9.12 `_filter_dc()` and Dataclass Construction

- Misspelled field names in YAML (e.g. `whisper.modle_size`) cause `TypeError: unexpected keyword argument`
- Fix: call `_filter_dc(raw, DataclassType)` to filter unknown keys before all `**raw` unpacking
- Note: `ScriptConfig` uses explicit kwargs construction, no filtering needed; `_parse_providers`/`_parse_tasks` use `.get()` for safe reading

### 9.13 Provider Cache and Test Isolation

- `_provider_cache` in `ai/factory.py` is a module-level global variable, persisting across tests
- If test A caches a provider, test B's `monkeypatch` config changes may still get the old provider
- Fix: `_clear_provider_cache()` + `conftest.py`'s `autouse` fixture auto-clears before each test

### 9.14 `retry_attempts` Semantics

- `ProviderConfig.retry_attempts` means **extra retry attempts** (excluding the first), default `2`
- `with_retry(attempts=N)`'s `attempts` means **total call count** (including the first)
- Conversion formula: `with_retry(attempts=cfg.retry_attempts + 1)`
- Both providers (gemini + openai_compat) use the same formula to keep semantics consistent

### 9.15 `cancel_event` Propagation in Pipeline

- This is fixed for current steps, but it remains a contract for new steps.
- New task functions should accept `cancel_event: threading.Event | None` when they do long-running work.
- Check cancellation inside the main loop and pass the event from UI/CLI route handlers through `run_pipeline_steps()`.
- Keep rerun, selected-file runs, and full pipeline behavior consistent.

### 9.16 `RateLimiter` Lock Reentrancy

- The original lock-sleep issue has been fixed, but keep this invariant: rate limiting should compute waits under a lock and sleep outside shared locks.
- Any new provider or parallel AI path should reuse the existing rate limiter pattern rather than adding ad hoc sleeps.

### 9.17 Whisper Download Thread Cancellation

- The unsafe `ctypes.pythonapi.PyThreadState_SetAsyncExc` approach has been removed.
- Keep model downloads chunked and cancellation-aware: stream downloads, check cancel flags between chunks, and clean partial files explicitly.
- Relevant modules are split across `clio/ui/routes/whisper_check.py`, `whisper_download.py`, and `whisper_models.py`.

### 9.18 `/api/fs/dirs` Security

- Path restriction and token auth are implemented. Preserve this contract when adding browse-like routes.
- When serving with `--host 0.0.0.0`, sensitive routes must require token auth.
- Keep route coverage in `clio/tests/test_fs.py` and `clio/tests/test_server.py` when changing filesystem browsing.

### 9.19 beforeStop Hook (`shutdown.py`)

- `install_hooks()` registers `atexit` + `signal(SIGTERM)` (Unix) to call `before_stop()` on shutdown
- `before_stop()` (idempotent): kills registered ffmpeg subprocesses → closes provider HTTP connections → flushes IO
- `register_process(proc)` / `unregister_process(proc)`: every ffmpeg subprocess creation must wrap with these to avoid orphaned processes on SIGTERM
- Currently integrated in: `run_ffmpeg()` (utils.py) and `_extract_audio()` (tasks/transcribe.py)
- Both `main.py` and `server.py` call `install_hooks()` at startup and `before_stop()` in their `finally` blocks
- Any new ffmpeg subprocess creation must follow the same register/unregister pattern

### 9.20 Split Segment Sidecar Mapping (`videos.py`) — ✅ Fixed (B-097, `05edab2`)

- **Fixed**: `videos.py` now uses a 3-strategy lookup chain for text/script sidecars:
  1. Exact compressed filename (v2+ data with `compressed_file` field)
  2. Compressed stem (no extension) — enables segment-specific matching
  3. Zero-padded index (v1 fallback)
- Script files additionally build a reverse map via `text_stem_to_compressed` to match `_voiceover` files back to their compressed stems
- The `_parse_segment_info` regex also expanded to support `_partNN`, `_ptNN`, `_chunkNN` (case-insensitive) in addition to `_segNN` (B-073, `f2465cd`)

### 9.21 Config Auto-Upgrade: Dataclass Defaults Injection

- `_upgrade_config_file()` in `loader.py` runs at the start of every `load_config()` call
- For each YAML section (`paths`, `proxy`, `ai`, `compress`, `analyze`, `naming`, `script`, `plan`, `whisper`), it checks the corresponding dataclass for fields not present in the YAML and injects their Python `field(default=...)` values
- Also covers `ai.providers.*` (per `ProviderConfig`) and `ai.tasks.*` (per `TaskConfig`)
- Handles both `config.yaml` and `project.yaml` independently
- `Path`-typed defaults are converted to strings via `str()` before YAML serialization to avoid unsafe `!!python/object` tags
- Writes back via `yaml.dump()` only when something changed; prints a summary to stdout
- **Trade-off**: PyYAML does not preserve comments — a one-time loss when new fields are injected
- Uses atomic write (tmp + `os.replace`) to prevent partial writes on crash

### 9.22 Sidebar Video Filter: Reset on Source Switch

- `state.videoFilter = { q, stage, mode }` must be reset whenever `state.source` changes —
  both in `sidebar.js setSource()` and the `proj.source` branch of `loadProject()`.
  A stale `stage='compress'` filter from the original view (未压缩 chip) yields a dead-empty
  list in the compressed view, where no such chip exists to recover.
- Chips filter by **missing** stage (`mode='missing'`), count-bar cells filter by **done**
  (`mode='done'`); keep the two modes mutually exclusive or the same cell toggles both.
- Helper predicates live in pure `sidebar-video-filter.js` (unit-tested); do not inline the
  same logic in `sidebar-data.js`.
- Only the user's local config files are touched; `config.example.yaml` is never modified

### 9.23 Never Start Real pywebview Under pytest

- `clio.desktop.app._reject_real_webview_under_pytest()` runs before `start_server` and
  refuses to open a real window when `PYTEST_CURRENT_TEST` is set.
- Desktop-host tests must inject `sys.modules["webview"]` with a fake module (no `__file__`),
  as `test_desktop_app.py::_install_fake_webview()` does.
- `CLIO_ALLOW_DESKTOP_UNDER_PYTEST=1` is an explicit escape hatch for intentional GUI tests;
  never set it in normal CI. A missed mock previously left multiple Clio/WebView2 windows and
  hung pytest processes because each temp config dir had its own lock and random API token.

### 9.24 Retry Only Tasks With Reconstructible Inputs

- `TaskManager.retry` persists and reuses `input_data`, but deliberately does not persist
  `private_input_data` (which may contain prompts, config objects, or secrets).
- A handler that depends on ephemeral private input must set `input_summary["retryable"] = False`
  and provide `retry_blocked_reason`; the manager then rejects retry and the Task Center hides it.
- Pipeline requests with `context_override` or `task_prompts` already use this contract. Without
  the flag, retry would silently run with different prompts/context and produce misleading output.
