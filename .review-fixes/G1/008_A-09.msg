fix(analyze): guard trip-context cache with a lock

_store_trip_context snapshotted the cache keys and then deleted matching
entries one by one without a lock. Two analysis tasks running in parallel
could delete the same key and raise KeyError, failing the AI call; lookups
had the same check-then-read race. Serialize all access through a module
lock and evict with pop(key, None).

Refs: A-09
