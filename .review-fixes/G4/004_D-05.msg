fix(ui): resolve texts/scripts in configured artifact subdirs

/api/videos lists analyses from analyze.texts_subdir and scripts from
script.scripts_subdir, but GET/PUT /api/texts, /api/voiceover and
/api/refine only looked in output/texts* and a hard-coded scripts/, so a
custom subdir (e.g. "analysis") gave 404 on open and 403 on save.
The routes now pass the project's configured subdirs to the resolvers
(resolve_texts gains preferred_subdir). Adds read/resolve tests.

Refs: D-05
