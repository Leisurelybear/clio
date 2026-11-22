fix(file-service): prune rotated backups without glob patterns

_prune_rotated_backups built a glob from the file name, so names with
[ or ] (allowed by _is_safe_basename, e.g. "001_trip[1].json") matched
nothing and every save left another .bak.<ns> behind forever.
It now lists the directory and filters by name prefix, and skips entries
that vanish or fail to stat. Adds a test with bracketed names.

Refs: D-07
