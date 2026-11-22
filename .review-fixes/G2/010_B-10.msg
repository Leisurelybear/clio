perf(task-center): skip progress writes that change nothing

ffmpeg progress callbacks fire on every stderr time= line, several times a
second, and each call ran a write transaction plus a task_events insert
even when phase, counters and message were identical. A one-hour encode
produced thousands of duplicate events. update_progress now returns the
stored task unchanged when nothing would change.

Refs: B-10
