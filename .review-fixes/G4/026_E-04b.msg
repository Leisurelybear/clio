feat(api): add atomic POST /api/videos/selected/remove

Removing videos was a client-side GET + full-list PUT of videos.json, so a
concurrent add or remove (second tab, video manager) could be overwritten.
New endpoint takes {paths:[...]} and reads, filters and writes videos.json
inside path_write_lock, returning {ok, removed, videos}. Registered in the
route table and exported from clio.ui.server for the lazy resolver.

Refs: E-04b
