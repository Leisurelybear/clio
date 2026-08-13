# P2-P38: Session logs polling lifecycle + ring buffer

Approved in chat 2026-08-13.

## Decisions

- Ring buffer capacity: **2000** entries (drop oldest).
- Overlapping polls: **skip** if previous `/api/logs` still in flight.
- Leave logs entity: stop interval only; **keep** buffer, offset, filters.
- Re-enter logs: paint existing buffer first, then incremental fetch from offset.
- Clear button: still clears server + local buffer + offset (unchanged semantics).

## Non-goals

- No `/api/logs` protocol change.
- No change to disk log retention.
