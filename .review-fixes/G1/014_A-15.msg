perf(run-scope): list compressed media once per scope resolution

_resolve_capture re-listed the whole compressed/ directory for every
analysis artifact that had no persisted capture date, making Run preview
O(N^2) in directory reads (slow on network shares). Build the unfiltered
compressed map lazily once per resolve_run_scope call, share it with every
_resolve_capture, and look the stem up by key instead of scanning.

Refs: A-15
