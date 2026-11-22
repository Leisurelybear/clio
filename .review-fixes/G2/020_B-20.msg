fix(analyze): match single-file compressed clips exactly

Without a .vindex, single-file re-analyze picked every compressed file
whose stem contained the original stem, so GH01 also matched 002_GH0123
and wrote another video's analysis under GH01. A clip now belongs to the
file when its .vmeta source path resolves to it, or (no usable .vmeta)
when its stem without index prefix / split suffix equals the stem.

Refs: B-20
