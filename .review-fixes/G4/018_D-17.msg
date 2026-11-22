perf(config-cache): make ConfigCache a real LRU

ConfigCache claimed LRU but never refreshed an entry on a hit, so the
most-used project config could be evicted first and reloaded (YAML parse,
credential resolution) on the next request; eviction also dropped the
per-key lock while another thread might hold it. Hits now move the entry
to the most-recent end and eviction keeps the key lock. Adds an LRU test.

Refs: D-17
