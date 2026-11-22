fix(notifications): back off SSE reconnects and stop on 401

The notification stream retried every 3s forever on any error, so an
expired token (401) hammered the server and logs indefinitely. Reconnects
now back off exponentially from 3s to 60s, reset after a successful open
or message, wait while the page is hidden, and after repeated failures
probe /api/notifications: on 401 the retry loop stops (api() already shows
the re-auth prompt, whose submit reloads the page). Tests added.

Refs: E-13
