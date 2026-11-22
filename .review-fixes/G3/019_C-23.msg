chore(git): ignore local runtime and scratch files

.gitignore missed several files that local runs leave in the repo root:
.env.* variants (keeping .env.example), SQLite WAL/SHM files of
task-center.sqlite3, parallel coverage data, the desktop clio.lock and
desktop-state.json, .tmp_* scratch scripts, *.patch and *.corrupt.* files.
Already tracked files still need git rm --cached on the user's side.

Refs: C-23
