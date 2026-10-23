# Analysis — Web UI Unreachable: SSE Streams Starved the Request Pool

- **Date:** 2026-10-23
- **Baseline:** `c766c85` (branch `main`; local commits only, not pushed)
- **Trigger:** user report — "运行后还是打不开" plus DevTools `failed to load response data` on
  `GET /api/video` and `GET /api/config/global`
- **Scope:** `clio/ui/http_server.py`, `clio/ui/server.py` connection/request admission
- **Preceding review:** [`2026-10-23-i18n-regression-scan.md`](2026-10-23-i18n-regression-scan.md)

## 1. Result In One Line

Not an i18n regression and not a frontend bug: the local HTTP server admits **one** bounded worker
pool for both short-lived API requests and long-lived SSE streams, so a browser left open all day (10-19 concurrent SSE streams
observed, two per tab) consumed most of the workers. When the pool was full the server closed new connections
**without sending any HTTP response**, which Chrome reports as `failed to load response data` and
which made the whole UI unreachable. Fixed by splitting the budgets and answering 503 instead of a
silent close.

## 2. Symptom

- The page sometimes does not load at all; reloading does not help.
- DevTools shows `failed to load response data` for ordinary requests
  (`/api/video?…&source=compressed`, `/api/config/global?project=…`).
- The same requests succeed when replayed with `curl` from a terminal — the failure is
  **load-dependent**, not endpoint-dependent.

## 3. Evidence

### 3.1 The server was holding 19 of 32 worker slots in SSE loops

`py-spy dump --pid <serve pid>` (Windows, same user) showed 19 request threads alive, every one of
them parked in an SSE poll loop:

```text
Thread 43468 (idle): "Thread-62 (_process_request_thread)"
    handle_get_notifications_stream (clio\ui\routes\notifications.py:147)
    ...
Thread 47860 (idle): "Thread-2 (_process_request_thread)"
    handle_get_tasks_stream (clio\ui\routes\tasks.py:167)
    ...
```

`notifications/stream` threads: 12, `tasks/stream` threads: 7 — and `Get-NetTCPConnection` reported
exactly 19 `ESTABLISHED` sockets on `127.0.0.1:8800`, held by 9 Chrome renderer processes plus Edge
(one browser window/tab set each). So these were **live, legitimate** streams: one notification
stream plus one task stream per open tab. They were not leaked sockets, and the clients were not
gone — the design simply charged them to the same ceiling as normal requests.

### 3.2 The pool ceiling was therefore ~13, not 32

Measured free capacity by opening SSE streams until a normal `GET /api/projects` was refused:

```text
free-slot capacity with the user's browser attached: 22   (32 - 10 held)
capacity with 34 idle keep-alive sockets held:       22   (idle sockets cost nothing)
```

The second line also rules out the "keep-alive sockets leak slots" theory: the handler closes
HTTP/1.0 responses, so idle sockets release immediately. Only in-flight requests (including streams)
occupy a slot.

### 3.3 Exhaustion produced an empty response, not an HTTP error

With the pool saturated, both `/api/projects` and `/api/video` returned **zero bytes** — the socket
was closed with no status line:

```python
# clio/ui/http_server.py (before)
def process_request(self, request, client_address):
    if not self._worker_slots.acquire(blocking=False):
        request.close()      # no response, no log line
        return
```

Chrome renders that as `net::ERR_EMPTY_RESPONSE` / `failed to load response data`. Nothing was
written to the access log either, so the failure left no server-side trace. Introduced by `7f219f9`
(GAP-P2-09) — a latent defect unrelated to the localization work.

### 3.4 Why those two requests

`/api/video` (a 4.4 MB 206 response) and `/api/config/global` are simply the requests that were in
flight when the page issued more parallel requests than the remaining free slots. Each tab holds
two streams for its whole lifetime, so free request capacity shrinks by two per open tab; once it
is gone, the next burst of page traffic is refused. The browser-side per-host cap is not the
binding constraint: in the post-fix check Chrome held 8-10 concurrent sockets on `127.0.0.1:8800`
without stalling.

### 3.5 Ruled out

- **Not a localization regression.** A real Chrome (fresh profile, CDP-driven) loaded the project
  page, switched videos, switched every nav pane and every inner tab, and toggled the language
  button with zero exceptions and zero failed requests.
- **Not an in-page request storm.** Bursts of `/api/projects` + `/api/notifications/stream` at ~8/s
  in `logs/2026-10-23-16.log` were reproduced by the diagnostic loop itself (one SSE open plus one
  `/api/projects` probe per iteration), not by the frontend.
- **Not a slow endpoint.** `/api/videos` (142 entries) answers in ~2 s; `/api/video` range handling
  is correct (`bytes=0-` → 206 with the full body, unsatisfiable → 416).

## 4. Fix

`clio/ui/http_server.py`:

- `RequestBudget` splits the ceiling into `MAX_CONCURRENT_REQUESTS = 32` (short-lived requests) and
  `MAX_CONCURRENT_STREAMS = 64` (long-lived SSE, `STREAM_PATHS`). Streams can no longer consume the
  request budget, and vice versa.
- `MAX_CONNECTION_THREADS = 96` still caps total threads, so a slow client cannot spawn unbounded
  threads; hitting that cap now answers `503` with a JSON body (`BUSY_RESPONSE`) and logs
  `[serve] … - 503 server busy … (connection limit)`.
- `BoundedThreadingHTTPServer` injects the budget into the handler class.

`clio/ui/server.py`:

- The handler charges the budget in `parse_request` (the path is known there, so streams and
  requests are classified correctly) and releases it in `handle_one_request`, i.e. exactly for the
  lifetime of one request.
- An exhausted budget answers `503 {"ok": false, "error": "server busy: too many concurrent
  requests"}` and closes, so the browser and `api()` see a real HTTP status.

## 5. Verification

| Check | Result |
|---|---|
| `pytest clio/tests/test_http_server.py` | 13 passed (budget split, 503 body, handler injection, invalid budgets) |
| `pytest clio/tests/test_http_sse_lifecycle.py` | 7 passed, including two new regressions: a held stream no longer blocks a normal request, and an exhausted request budget answers 503 JSON |
| Live pressure test (server on `:8801`, new code) | 60 open streams → `/api/projects` 200 **and** `/api/video` 200/206; 65th stream → `503` JSON; after closing streams → 200 |
| Live connection-cap test | 64 streams + 40 silent connections → new requests get `HTTP/1.1 503 Service Unavailable`, logged as `503 server busy (connection limit)`; after cleanup → 200 |
| Live post-restart replay (`:8800`, fix loaded) | the user's two failing requests answer `206` (4,460,765 B) and `200` (1,500 B) |
| Live stream-isolation check (`:8800`) | with 40 concurrent SSE streams held, `/api/video` 206, `/api/config/global` 200, `/api/projects` 200; identical after the streams close |
| Multi-tab browser check (headless Chrome, 4 tabs / 8 streams) | every tab: `/api/video` 206 in ~30 ms, `/api/config/global` 200, `/api/projects` 200, 0 console errors |
| Full `pytest clio/tests -q` | 2410 passed, 12 skipped |
| `npm test` | 74 files / 753 tests passed |
| `ruff format` / `ruff check` on touched modules | clean |

The rows on `:8801` were verified on the patched code before the live `serve.ps1` instance
(started 16:07 with the pre-fix code) was restarted; the `:8800` rows above were taken after that
restart, so the fix is what the user's browser now talks to.

## 6. Side Findings

1. **`POST /api/notifications` always 400** — fixed in the following commit.
   `notification-center.js::_contextLink()` puts the Windows `project_dir` into `link`, and
   `routes/notifications.py` rejected any link containing a backslash, so every UI toast, status
   and runtime-warning notification failed to persist. The guard now inspects only the part before
   `?`/`#` — the only place a backslash can collapse to `//` and leave the origin — so `/\evil` and
   `/%5Cevil` stay rejected while a percent-encoded `project_dir` in the query is accepted.
   Verified live: the POST that answered 400 now answers 201, and a link with `%5C` in the path
   part still answers 400.
2. **SSE polling cost.** Each stream wakes four times per second and queries SQLite
   (`latest_notification_seq` / `events`). 19 streams ≈ 76 queries/s; the new stream budget allows
   up to 64 streams ≈ 256 queries/s. Consider a longer poll interval or a change signal.
3. **Two streams per tab is inherent.** The notification centre and the task centre each own an
   `EventSource`; sharing one channel would cut stream count in half.

## 7. Test Artefacts

`project.json` in the sample project records `lastEntity` / `lastVideo` for pane restore; the CDP
probes drove the real UI and updated it (`lastEntity: "video"`, `lastVideo: "008_GX010687.mp4"`).
It was restored to `lastEntity: "run"`, the value it held before the probes.