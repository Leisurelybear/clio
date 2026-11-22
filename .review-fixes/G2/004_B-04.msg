fix(migrate): keep projects.json intact when updating the registry

The registry was rewritten with a plain write_text, a corrupt projects.json
was silently treated as empty (wiping every project on the next write), and
dict entries plus unknown top-level keys were flattened or dropped.
Migration now refuses to run when the registry cannot be parsed, updates
the loaded dict in place (keeping entry metadata and other keys), and
writes it with write_json_atomic.

Refs: B-04
