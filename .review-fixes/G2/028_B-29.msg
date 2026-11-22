fix(task-center,waveform): bound per-key lock tables

TaskManager created a lock entry for every task ID it was asked about,
including IDs it never ran (recovered rows, request_cancel on foreign
IDs), and waveform kept one lock per cache key forever, so both tables
grew without bound. Tasks run by this process keep their dedicated lock;
everything else, and all waveform keys, now use a fixed pool of 64 locks.

Refs: B-29
