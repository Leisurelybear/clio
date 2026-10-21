# R-027e Historical Log Browsing Summary (2026-10-22)

- **Roadmap item:** R-027e
- **Status:** Complete locally; not pushed
- **Commits:** `22d9c80`, `eaae223`, `05ac502`

## 1. Outcome

The Web UI log panel can now switch between the in-memory live session and one hourly file written
under `paths.logs_dir`. Users no longer need to leave the app and grep `logs/YYYY-MM-DD-HH.log`
when investigating a previous run.

Live and historical views share the existing keyword, level, timestamp, badge, and auto-scroll
behavior. Historical mode is read-only: it pauses live polling and disables the destructive clear
action. Switching back to `当前会话` resumes polling from the retained sequence offset.

## 2. API Contract

| Endpoint | Result |
|---|---|
| `GET /api/logs/files` | `{"files":[{"name","size","mtime"}]}`, newest first |
| `GET /api/logs/file?name=YYYY-MM-DD-HH.log&max_bytes=2097152` | File metadata plus `{"logs":[{"ts","text"}],"truncated","bytes_read"}` |

Invalid names return HTTP 400. Missing files return HTTP 404. Filesystem read failures return
HTTP 500 without exposing the logs path.

## 3. Safety Decisions

- Only direct children matching the exact hourly basename are accepted.
- Paths are resolved and must remain under the resolved logs directory; symlink entries are
  rejected.
- The API reads only the file tail. The default is 2 MB and the hard cap is 5 MB regardless of
  the requested `max_bytes`.
- When a tail starts mid-line, the partial first line is discarded. UTF-8 decode errors are
  replaced rather than failing the request.
- `YYYY-MM-DD HH:MM:SS` prefixes are converted to local epoch seconds and removed from `text`;
  continuation lines inherit the previous timestamp.
- An absent or unreadable logs directory lists as empty instead of crashing the UI.

## 4. Frontend Behavior

- Source selector: `当前会话` plus available hour files labeled with local wall-clock hour and size.
- Historical mode stops polling, shows loaded-line count, and marks size-capped responses with
  `仅显示文件末尾`.
- Poll and file-load generations prevent stale requests from writing into a newer source view.
- The live buffer and offset remain intact while browsing history, so returning to live fills the
  gap without reloading the whole session.
- The existing client filters operate on the active source without changing filter state.

## 5. Verification

| Check | Result |
|---|---|
| Focused Python tests | `test_log_history.py` and log route tests pass |
| Frontend log tests | `logs-history.test.js`, `logs-ui.test.js`, existing filter/buffer tests pass |
| Full Python suite | `2070 passed, 12 skipped` locally on Python 3.10.6 |
| Full Vitest | `54 files`, `540 tests` passed |
| Ruff / mypy | Ruff clean across `234` files; mypy clean across the 39-file configured scope |

The Python 3.11/3.12 CI matrix remains pending because the batch has not been pushed.

## 6. Deferred Follow-ups

- Merge multiple hours or load an explicit from-to range in one request.
- Add a manual refresh action for the currently opened hour file.
- Copy/export filtered historical lines.
- R-027c server-side `?q=` filtering remains deferred until the client buffer becomes a measured
  bottleneck.
