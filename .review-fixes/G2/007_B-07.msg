fix(cut): refuse to overwrite a leftover .clio_bak on re-cut

replace_file_safely deleted an existing .clio_bak before backing up the
target. That backup is the only good copy left by an interrupted re-cut,
so re-running without restoring first destroyed it and kept the
half-written target as the new "backup". It now raises
CutBackupConflictError, matching _move_stale_cut_artifacts.

Refs: B-07
