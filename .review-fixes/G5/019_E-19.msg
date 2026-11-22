perf(logs): update live log counts and ring trim incrementally

Every 2s live-log ingest ran filterLogEntries over the whole buffer twice
(empty hint + match count), and once the 2000-entry ring was full every
poll repainted the entire view. Keep a running visible count (full recount
only for the clock-relative "since" filter), drop the DOM rows of trimmed
entries instead of repainting, and skip polls while the page is hidden
(the offset-based tail catches up). Regression test added.

Refs: E-19
