# Clio — HTTP Server & API Layer

> Series: `09` of `00-overview.md`. Covers `clio/ui/server.py` and `clio/ui/routes/` (REST +
> SSE), plus the `Router` in `clio/ui/router.py`. The web frontend is `10-ui-frontend.md`;
> the desktop host is `11-desktop-shell.md`.

## 1. Responsibilities

- Serve the web UI (stdlib `http.server`, no build step) and a JSON REST API on the local
  loopback interface for the desktop app / CLI UI.
- Dispatch requests through a small regex **Router**, enforce HTTP auth + local-session policy,
  parse JSON bodies with size/media-type guards, and send typed errors as JSON.
- Bridge browser and backend: long-running work is handed to `TaskManager` (see
  `08-task-center.md`); live updates flow over **SSE signals** that tell the client **to
  re-fetch** rather than pushing full payloads.

## 2. Module Map

| File | Responsibility |
|---|---|
| `server.py` | `make_handler` (`:259`) builds a `BaseHTTPRequestHandler` subclass; `do_GET/do_HEAD/do_PUT/do_POST/do_DELETE` (`:515-633`); `_require_auth` (`:362`, Bearer/`?token=`), `_require_local_session` (`:384`); `_read_json_body` (413/400/415); `run` (`:810`), `_loopback_hosts` (`:775`), `shutdown_task_manager` (`:767`) |
| `router.py` | `Router` (`:31`) — `add_list`/`dispatch`; `{param}` path params, prefix matching, per-route `auth_required`; `Route`/`RoutePolicy` (`:23`/`:15`); default policy `auth_required = method in {PUT, POST}` |
| `handler_protocol.py` | `HandlerProtocol` — the handler attribute surface consumed by route modules (`_send_json`, `config`, `project_dir`, task manager, …) |
| `routes/` | Focused modules per domain: `videos`, `texts`, `plan`, `run`, `tasks`, `projects`, `config_routes`, `env_routes`, `export`, `waveform`, `transcripts`, `refine`, `prompts`, `notifications`, `whisper_*`, `fs`, `deps`, `ai`, `token_routes`, `processing_state_routes`, `static_files` |

## 3. Lifecycle & Handler State

```text
run(host, port, api_token, …)
  ├─ make_handler(config, config_path, api_token, bound_host, bound_port, enforce_local_session,
  │              task_manager=None)
  └─ Handler closure captures: config, project_dir, output_dir, static_dir,
       _api_token, _allowed_hosts, _enforce_local_session, _enforce_json_ct
  ├─ ClassVars: _project_states (per-project _ServerState), _config_cache,
  │             _task_manager (lazily created TaskManager + builtin handlers)
  └─ per request: parse_request → _require_local_session → dispatch → _invoke_handler
```

- **Auth**: token empty ⇒ disabled; otherwise `HMAC compare_digest` over `Authorization: Bearer`
  or `?token=` query. Desktop/CLI UI frontends automatically carry the token.
- **Local-session policy**: `enforce_local_session` binding check (peer address must be an
  allowed loopback host) for the desktop/local server.
- **Security hardening**: oversized headers → 431; JSON content-length/media-type enforced before
  dispatch; `log_message` redacts `token=` query values; `?token=` split keeps secrets out of the
  status line that ends up in access logs.
- **Body handling**: `_read_json_body` returns `(-400 invalid, 413 too large, 400 bad
  content-length, 415 wrong media type)` and sends the matching `{ok:false, error}` before the
  handler ever runs.

## 4. Routing

```text
dispatch(method, path) → (handler_fn?, path_kwargs, route?)
  ├─ static: / , /index.html, /favicon.ico, /static/{rel}
  └─ regex table per method; routes declare {param} names captured into path_kwargs
     e.g. GET /api/videos/{stem}, POST /api/tasks/{task_id}/cancel
```

Handlers receive `(handler, qs, [obj], **path_kwargs)`; `qs` is `urlparse.parse_qs` so repeated
params survive as lists. Errors are JSON — a 404 body is `{ok:false, error:"unknown endpoint"}`
for API methods, `send_error(NOT_FOUND)` only for unknown static paths.

## 5. SSE Live Updates (`routes/notifications.py:96`)

The store keeps a monotonic `seq`; the SSE endpoint is a **refresh signal**:

```text
GET /api/notifications/stream?after={seq}
  → text/event-stream, id: {seq}, data: {"seq":…, "refresh": true}
  → : heartbeat  every 10s; poll store every 0.25s
client: on refresh, re-fetch GET /api/notifications + GET /api/tasks
```

- Reconnect-safe: `Last-Event-ID` + `after` cursor, so a dropped connection resumes at the highest
  known seq without missing events.
- The same cursor pattern powers the **unread badge**; read/unread state lives only in the store
  (`mark_notification_read`, R-042).

## 6. Route Groups (sample)

| Group | Endpoints (representative) |
|---|---|
| videos | `GET/PUT /api/videos`, `PUT /api/videos/relink`, `GET /api/videos/selected`, `GET /api/vmeta/{stem}` |
| texts / transcripts | `GET/PUT /api/texts`, `GET /api/voiceover/{stem}`, `GET /api/cover/{stem}`, `GET/PUT/POST /api/transcripts` |
| plan | `GET /api/plans`, `GET/PUT /api/plans/{name}`, `POST /api/plans/{name}/readiness`, `POST /api/cut…` |
| run | `POST /api/run/start|preview`, `POST /api/run/rerun` (pipeline + step runners) |
| tasks | `GET /api/tasks`, `GET /api/tasks/{id}`, `POST /api/tasks/{id}/cancel|retry`, `GET /api/tasks/stream` |
| config | `GET/PUT /api/config/global|project`, `GET /api/config/schema`, providers CRUD, `GET/PUT /api/env` |
| projects | select/create/add/remove/migrate; `GET /api/processing-state` |
| whisper | install status/start/cancel, model download/delete, dependency checks |
| export | `POST /api/export` (see `12-export.md`) |
| fs | dirs/entries/mkdir/reveal (path-picker widgets) |
| misc | `POST /api/ai/test`, `GET /api/token-usage`, `GET/PUT /api/prompts`, `GET /api/logs`, notifications inbox |

## 7. Cross-Module Dependencies

- → `clio/task_center` (`TaskManager`, `TaskStore`, `Notification`) — run + inbox
- → `clio/config` (config split read/write, schema, provider registry)
- → `clio/tasks/*` (invoked via submit handlers / deferred runs)
- → `clio/ai` + `clio/asr` (test endpoints, whisper install, model management)
- → `clio/identity` + `clio/vmeta` + `clio/index` (video/text/transcript/cover lookups)
- → `clio/plan_model` + `clio/plan_readiness` (save validation / readiness gate)

## 8. Risks / Maintenance Notes

1. `do_GET`/`do_HEAD`/`do_PUT`/`do_POST`/`do_DELETE` hand-roll the auth + body + dispatch order;
   a new HTTP method added later must replicate the same three gates (local session, auth,
   body parse) — keep behavior mirrored in tests.
2. SSE pushes a **refresh signal**, not data; clients must re-fetch after `refresh:true`. A
   client that ignores the signal goes permanently stale (heartbeat-only) — the frontend SSE
   module owns this contract (`notification-center.js`).
3. `Router.get_policy` defaults `auth_required=True` for unknown paths, but `dispatch` DEFAULTS
   to `auth_required=True` only in the `RoutePolicy` default; explicit routes must set
   `auth_required=False` for truly public paths (e.g. static assets) or they’ll 401 in browser.
4. The `?token=` query form is kept for CLI/iframe flows, but tokens can leak into browser
   history/server logs; the desktop shell now prefers `Authorization` + `_require_local_session`.
5. Large JSON bodies → 413 and big query strings stay bounded; media serving relies on the
   Host-range/HEAD support in `do_HEAD` (P2-P40) — new file-serving routes should reuse it
   instead of re-reading whole files into memory.