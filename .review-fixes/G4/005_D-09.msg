fix(config): merge global PUT into config.yaml instead of replacing it

GET /api/config/global hides project-only sections and the project keys
of split sections, but PUT wrote the client object as the whole file, so a
simple language switch deleted paths.output_dir, compress/whisper/analyze
defaults, etc. (the new-project template and global fallback).
PUT now overlays the global-layer data on the on-disk config, keeping the
hidden fields and key order. Adds merge and round-trip tests.

Refs: D-09
