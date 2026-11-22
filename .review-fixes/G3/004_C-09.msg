fix(ai): share the token usage lock between store instances

Every task creates its own FileTokenUsageStore, and the lock was per
instance, so analyze and refine running together could interleave their
read-merge-write cycles on .token_usage.json and drop each other's records.
The store now uses utils.path_write_lock for its file, shared by every
instance in the process. Cross-process writers (CLI next to UI) remain.

Refs: C-09
