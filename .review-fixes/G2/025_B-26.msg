perf(reindex): parse videos.json once per run instead of per file

Startup reindex resolved every compressed file's original through
_find_original_for_stem, which re-read and re-parsed videos.json each
time (O(N) file reads per check). The parsed list is now cached keyed by
the file's path, mtime and size, so edits are still picked up.

Refs: B-26
