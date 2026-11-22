fix(config): keep list fields as arrays when editing settings

A list[str] value is edited in a "one per line" textarea, but write-back
stored the raw multi-line string, changing the YAML type to str. Object
array items used `path[i]` paths that setDeep() turned into a literal
"tags[0]" key. Mark the textarea as data-kind="str-list" and split it on
\r?\n (trimmed, blanks dropped); use dot-separated indexes for array
items so edits land in the array. Regression test added.

Refs: E-18
