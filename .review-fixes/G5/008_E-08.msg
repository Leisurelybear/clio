perf(tasks): coalesce SSE re-renders and keep open filters intact

Every task SSE event rebuilt the whole task pane via innerHTML. Bursts of
progress events burned CPU, closed a filter dropdown the user had open
and reset scroll positions. Stream-driven renders are now coalesced to one
per animation frame, postponed while a toolbar <select> has focus, and
list/event scroll offsets are restored after a rebuild. Tests added.

Refs: E-08
