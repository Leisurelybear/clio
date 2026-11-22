fix(reindex): index every same-stem original separately

Two originals sharing a stem land in one reindex group; only the first source
was indexed and the startup check treated the stem as done, so the other
original's clips never got a .vindex. After indexing the primary source,
reindex now writes a separate (hash-suffixed, see vmeta) .vindex for each
other existing source named by a .vmeta, and auto_reindex rebuilds a stem
when such a source is not indexed yet.

Refs: B-03b
