fix(ui): fix status timer, null cancel, stale detail error, row menu

Four small races: setStatus kept no timer handle, so the timer of an
earlier identical message cleared the newer one early; cancelRun POSTed
/api/tasks/null/cancel when no task was managed; a stale selectTask error
overwrote the status of a newer request; the portal row menu survived
list re-renders with closures over replaced rows. Keep one status timer,
guard cancelRun, check the request generation in the catch branch, and
close the row menu at the start of renderVideoList(). Tests added.

Refs: E-24
