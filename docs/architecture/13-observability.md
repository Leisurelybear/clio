# Clio — Observability (Logs, Progress, State, Privacy)

> Series: `13` of `00-overview.md`. Covers `clio/log.py`, `clio/session_log.py`,
> `clio/progress.py`, `clio/processing_state.py`, and `clio/privacy.py` — how Clio records what it
> did, where it is in the pipeline, and what it is careful *not* to record. Series concludes
> here; back to `00-overview.md` for the full map.

## 1. Responsibilities

- Unified logging: mirror all `print`/exception output to both console and hourly-rotating,
  privacy-redacted, quota-bounded disk logs.
- Live UI console: an in-memory **session log** the web UI tails (offset-based `GET /api/logs`).
- Progress + state: `.progress.json` (current run) and `.processing.json` (per-file step matrix)
  let the UI/CLI resume and summarize without holding process state.
- Privacy: credentials (Bearer, `sk-*`, URL userinfo, `key=…`) are redacted *before* lines reach
  durable storage.

## 2. Module Map

| File | Responsibility |
|---|---|
| `log.py` | `setup_logging` (`:210`, idempotent) / `teardown_logging` (`:246`); `_HourlyFileHandler` (`:30`) — rotates `logs/YYYY-MM-DD-HH.log`, 64MB quota, 5-consecutive-failure mute; `_TeeWriter` (`:138`) mirrors stdout→INFO / stderr→WARNING and feeds `session_log`; `_install_excepthook` (`:191`) folds uncaught tracebacks into one ERROR; `timed` (`:306`), `format_size`/`format_duration` (`:282`/`:293`), `clear_disk_logs` (`:268`, UI "clear logs") |
| `session_log.py` | Ring buffer (10k entries) of `{ts, text, seq}`; `write`/`read(offset)`/`clear`; consumed by `/api/logs` |
| `progress.py` | `ProgressTracker` (`:30`) — thread-safe `.progress.json` writer: `phase/current/total/message/status/eta_sec`, atomic tmp-replace, ETA from elapsed rate, `logs` tail (100) |
| `processing_state.py` | `ProcessingState` (`:35`) — per-file `{step: status}` matrix in `.processing.json`, debounced flush (0.5s, 20 pending, `atexit` weakref sweep), `mark`/`reset_step`/`get_state` |
| `privacy.py` | `redact_sensitive` (`:17`) — masks `://user:pass@`, `Authorization: Bearer`, `api_key=…`/`token`/`secret`/`password` assignments, `sk-…` tokens; truncates lines > 4096 chars |

## 3. Logging Flow

```text
print(...) / traceback / logging.*
   └─ _TeeWriter.write → original stream (console)
        │              └─ logger.log(level, redact(line))
        └─ session_log.write(redact(line))          # UI console tail
   └─ _HourlyFileHandler.emit
        ├─ 64MB quota per hour file → pause + one-time warning
        ├─ 5 consecutive I/O failures → mute file logging (stderr fallback)
        └─ redact_sensitive(formatted record) + "\n"  # disk write
sys.excepthook → single ERROR record (traceback kept intact), avoids per-line spam
```

Details worth copying elsewhere:

- **Rotation is time-based, not size-based**: `_HourlyFileHandler._rotate` keys on
  `YYYY-MM-DD-HH` and re-opens on the hour — no restart needed.
- The handler writes straight to its own `TextIO` (append, `chmod 0o600`), not through
  `logging` formatter classes, so a disk problem cannot spin the logger.
- `_TeeWriter` swallows `close/writelines/detach` (`__getattr__` guard) so framework code that
  expects a normal stream never breaks; `print("a\n")` splitting is de-duplicated so empty lines
  don’t create bogus session-log rows.
- `teardown_logging` exists so pytest’s `capsys` recovers the raw streams.

## 4. Progress & Processing State (UI + CLI shared files)

`.progress.json` — the *current active run* (CLI & UI read it to rehydrate after refresh):

```json
{ "_schema_version": N, "phase": "analyze", "current": 3, "total": 8,
  "message": "...", "status": "running", "started_at": "...",
  "eta_sec": 214, "rerun": false, "rerun_video": null, "logs": [] }
```

`.processing.json` — a *persistent per-file matrix* that survives restarts:

```json
{ "_schema_version": N, "version": 1,
  "steps": ["compress","analyze","voiceover","transcribe","plan","label"],
  "files": { "GL010683": { "compress": "done", "analyze": "ok", … } } }
```

- `ProgressTracker` computes ETA from `current/elapsed` rate and always flushes via temp-file +
  `os.replace` (atomic) — a crash leaves the last good snapshot.
- `ProcessingState` debounces disk writes (dirty flag + 0.5s flush thread + 20-pending cap) and
  registers a process-wide `atexit` sweep (`weakref.WeakSet`) so no instance is ever lost.
- Both are read by `routes/processing_state_routes.py` / `GET /api/processing-state` and shown by
  the UI after reload; the Task Center (see `08-task-center.md`) supersedes these for *managed*
  runs but the files remain the CLI + legacy contract.

## 5. Privacy Pipeline

`redact_sensitive` runs at **two** boundaries: `_TeeWriter` (before console-logger + session log)
and the file handler (before the disk write). It is line-oriented and regex-based:

```text
Authorization: Bearer xxx    → Authorization: Bearer ***
sk-AAAA…                    → sk-***
https://user:pass@host       → https://***:***@host
api_key=foo / token:bar      → api_key=***
line > 4096 chars            → truncated with "[已截断 N 字符]"
```

- Applied to every CLI `print` and every disk log record blindly — cheap and broad, favors
  over-redacting.
- Server access logs additionally strip `token=` before printing (`server.py:log_message`).

## 6. Cross-Module Dependencies

- Consumers of `setup_logging`: `main.py`, `clio/ui/server.run`, `clio/desktop/app.main`
- `session_log` is read by `routes` (logs endpoint) — served to the web UI log pane
- `ProgressTracker` ↔ legacy step functions and `TaskProgressReporter` bridge (08)
- `ProcessingState` used by compress/analyze/… runners and `processing_state_routes.py`
- `privacy` used by `log.py`, `server.py`, and anywhere a raw credential line could escape

## 7. Risks / Maintenance Notes

1. `.progress.json` and `.processing.json` live in `output/` next to real media; a very long run
   rewriting the ETA every 0.5s is disk-friendly but still *a* write per update — bulk batch
   updates should use `ProgressTracker.update` once per item, not per sub-line.
2. Hourly file rotation means a long sprint at midnight splits one logical run across two files;
   UI log-filtering must merge `*.log` across hours, and `clear_disk_logs` only clears `*.log`.
3. Redaction is regex-based; a credential format outside the patterns (e.g. JSON `"password":"…"`
   with no `=`/`:` after the key, or Bearer in uppercase header variants) can slip through —
   the system prompt’s "no secrets in config/examples" rule is the backstop.
4. `_TeeWriter.__getattr__` explicitly refuses `close` — module teardown does not close streams;
   callers must use `teardown_logging()` (pytest) or let the process exit, otherwise Windows file
   handles stay open.
5. `ProcessingState.steps` is a fixed six-step list; adding a pipeline step requires an update
   here **and** in the UI stage chips (`sidebar-video-filter.js`) or the matrix/UI drift.