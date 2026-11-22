fix(sse): treat write timeouts as client disconnects

SSE writes run under the 60 s socket timeout. When a client stops reading
(frozen background tab, sleep) wfile.write raises TimeoutError, which the
stream handlers did not catch, so _invoke_handler printed a full
[http-error] traceback for a normal disconnect.
The task and notification streams now also end quietly on TimeoutError.
Adds tests for both streams.

Refs: D-24
