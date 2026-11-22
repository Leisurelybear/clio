fix(tasks): rebuild the task SSE stream after it closes

The task EventSource had no onerror. When a reconnect got a non-200
answer (401 after a server restart, 5xx) the stream went CLOSED for good
while _stream stayed set, so startTaskStream() never rebuilt it and
waitForTask(), rerun/runner progress and installers hung forever.
On CLOSED, drop the stream and reconnect with 1s..30s backoff from the
last cursor, then reload the task snapshot. Regression test added.

Refs: E-07
