fix(compress): write .vindex for finished files when a later one fails

run_compress_all wrote .vindex files only after the whole loop. When the
k-th compress failed or was cancelled, the k-1 fresh outputs had a .vmeta
but no .vindex until the next startup reindex filled them in. The index
for the files finished so far is now written before the error propagates
(best effort, so it never masks the original error).

Refs: B-24
