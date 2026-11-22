perf(routes): compute media index aliases once per readiness check

Plan readiness, cut and export routes called collect_project_indices and
collect_project_media_durations without media_aliases, so each of them
rescanned texts/ and resolved every compressed identity again.
Compute collect_media_index_aliases(cfg) once per request and pass it to both
collectors (parameter added by A-16 in plan_readiness). Test doubles in the
route tests now accept the keyword argument.

Refs: A-16b
