fix(config): serialize and roll back project.yaml writes atomically

PUT /api/config/project wrote project.yaml, validated, and on failure
restored it with a plain write_bytes, without the store lock; the whisper
model PUT did its own unlocked tmp+replace with sort_keys=True, so the two
could lose each other's updates. Both now share project.yaml's path lock,
the rollback is atomic and keeps .bak, config PUT is serialized like the
raw PUT, and the whisper writer uses _save_atomic keeping key order.

Refs: D-10
