fix(vmeta): keep a separate .vindex per same-stem original

.vindex files were named after the source stem only, so a second original
with the same file name in another folder could never get its own index.
VideoIndex.write now falls back to <stem>.<hash8>.vindex when <stem>.vindex
belongs to another source; read() gains segment_filename/source_path filters
that also scan hashed siblings (unfiltered reads are unchanged). identity,
_helpers and analyze pass the filters. Reindex follows in G2 (B-03b).

Refs: B-03b
