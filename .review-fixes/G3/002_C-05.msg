fix(config): abort V1 migration when the backup cannot be written

The V1->V2 migration swallowed backup write errors and then overwrote
config.yaml, or deleted a project.yaml with no project-only fields. It also
skipped the backup whenever an older .bak existed, leaving a stale snapshot.
Backups are now written byte-for-byte, get a timestamped name when a .bak
already exists, and a failed backup skips the migration of that file.

Refs: C-05
