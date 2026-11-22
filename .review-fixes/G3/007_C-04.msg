fix(credentials): log and report why the OS keyring is unavailable

Keyring discovery ended in "except Exception: pass", so a missing package,
an import error or a rejected plaintext backend all silently degraded to
.env with nothing to debug. Discovery now records a reason (exception type
only, never its text), logs it at info level, and describe() returns it so
capabilities() can show the UI why keyring storage is disabled.

Refs: C-04
