fix(reindex): never overwrite foreign or stale .vmeta sidecars

Reindex groups compressed files by source stem. A .vmeta that pointed at
another original with the same stem, or that was stale, was rebuilt against
the group's source and rewritten, so outdated output looked fresh to
compress reuse. Such sidecars are now left untouched and reported; a group
with stale files gets no .vindex, and foreign files are left out of it.

Refs: B-03
