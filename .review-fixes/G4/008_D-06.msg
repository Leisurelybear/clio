fix(file-service): keep primary file in place while writing .bak

_save_atomic moved the primary to .bak before renaming the temp file in,
so for a moment the file did not exist: unlocked readers (resolve_texts,
load_config, the CLI) saw 404/defaults, and a crash there left only .bak.
The backup is now a hard link (or copy) of the primary and the new content
replaces it with a single os.replace. Adds visibility/fallback tests.

Refs: D-06
