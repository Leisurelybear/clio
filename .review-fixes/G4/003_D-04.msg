fix(refine): avoid lost edits and duplicate runs in /api/refine

A refine (often tens of seconds) wrote its result unconditionally, so an edit
saved via PUT /api/texts meanwhile was overwritten; the busy check and mark
were two separate lock steps, so two refines could run on one file.
Now the busy flag is claimed atomically, the write passes the version read
before the AI call (409 + refined data on conflict), texts keep server-owned
fields, and the .txt/.md sidecars are rewritten like the CLI refine.

Refs: D-04
