fix(ai): keep corrupt token usage files and backfill missing fields

A corrupt .token_usage.json was read as empty stats and the next record
overwrote it, losing all history without a word. Files missing by_model,
by_task or total keys made _merge_stats raise KeyError on every AI call.
Unreadable or wrongly shaped files are now renamed to .corrupt.<ts> with a
warning, and every stats/bucket field is backfilled from the empty template.

Refs: C-10
