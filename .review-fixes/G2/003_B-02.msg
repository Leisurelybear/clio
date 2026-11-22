fix(task-center): only notify on failure for background tasks

Every task reaching a terminal state produced an inbox notification,
regardless of visibility. Background waveform tasks run on every video
view, so the inbox filled with "succeeded" entries. Notifications for
BACKGROUND tasks are now created only for the FAILED status event.

Refs: B-02
