refactor(tasks): share one renderer for analysis .txt files

_write_text_file and _rewrite_text_file were ~90% copies, and only the
latter rendered the _changelog section, so rewriting a .txt through
_write_text_file (e.g. after transcript alignment) dropped the refine
change log. Both now build their lines with _analysis_md_lines(); the only
difference left is the optional compressed-file line.

Refs: B-27
