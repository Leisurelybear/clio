perf(readiness): compute media index aliases once per cut/export

A cut/export readiness check parsed every texts/*.json four times and
resolved each compressed identity twice, because collect_project_indices
and collect_project_media_durations each called collect_media_index_aliases.
Let both accept a precomputed media_aliases mapping (optional, so existing
callers are unchanged) and pass one shared result from the CLI.

Refs: A-16
