fix(analyze): ignore CWD and tolerate non-UTF-8 trip_context.md

With no project_dir, _read_trip_context resolved Path("") to the process
CWD and injected CWD/templates/trip_context.md as the binding system spec.
A project template saved as ANSI/GBK raised UnicodeDecodeError on every AI
call. Skip the project lookup without a project_dir, read with utf-8-sig and
fall back to the packaged default (with a warning) on decode errors, and key
the cache on mtime_ns + size instead of the float mtime.

Refs: A-10
