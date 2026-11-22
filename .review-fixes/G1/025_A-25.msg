fix(plan): keep _schema_version and reject non-finite plan numbers

_schema_version was a known key, so it never landed in extras, but
to_dict() did not write it back either: every Plan.from_dict().to_dict()
round-trip dropped the version marker. total_estimated_sec and confidence
also accepted NaN/inf, which json.dumps writes as bare NaN that the UI cannot
parse. Keep schema_version on Plan and write it back; fall back to the
default total / no confidence for non-finite values.

Refs: A-25
