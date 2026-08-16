# R-042 Persistent Notification Center Design

**Status:** Implemented on 2026-08-17.

## Goal

Keep user-facing completion, warning, and error messages after a toast disappears
or the user switches pages/projects. The inbox complements Task Center history; it
does not replace task lifecycle state.

## Sources

- Task Center terminal status events: succeeded, failed, cancelled, interrupted.
- Every Task Center warning/error event, including warning/error log events.
- Existing frontend status/toast messages, including non-task actions such as save,
  relink, import, restore, and validation failures.
- Persistent runtime warnings such as missing ffmpeg/API keys and orphaned cut
  backups.

Task notifications are created in the same SQLite transaction as their source
event. Frontend notifications use the authenticated notification API. Source
keys and a unique `dedupe_key` make repeated registration idempotent.

## Data Contract

Each notification stores a global sequence, stable ID, severity, title/message,
creation/read timestamps, source identity, optional task/project identity,
optional local deep link, and small JSON metadata. Allowed severities are
`info`, `success`, `warning`, and `error`.

Unread notifications are never removed by automatic cleanup. Read notifications
follow Task Center retention and count limits, so the inbox remains bounded
without deleting messages the user has not handled.

## API

- `GET /api/notifications`: list with unread/severity/project filters and cursor.
- `GET /api/notifications/stream?after=<seq>`: resumable global SSE stream.
- `POST /api/notifications`: register frontend-originated messages.
- `POST /api/notifications/{id}/read|unread`: update one read state.
- `POST /api/notifications/read-all`: mark the selected scope as read.

All endpoints use the existing local-session and API-token policy. Notification
links must be local paths; task links open Task Center detail directly.

## UI

The global header bell shows the total unread count. Its inbox provides All,
Unread, and Warning/Error filters, read actions, realtime insertion, and task
deep links. The inbox remains available regardless of the active editor entity.

Desktop OS notifications, email, webhooks, and remote push are outside this
release. The persistent in-app inbox is the delivery source of truth.
