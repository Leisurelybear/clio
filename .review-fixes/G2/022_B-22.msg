fix(refine): do not overwrite edits saved while the LLM was refining

Refine read the JSON, waited tens of seconds for the LLM and then wrote
the result unconditionally, silently discarding any edit saved from the UI
in the meantime. Texts and scripts refine now compare the file with the
bytes they started from and skip the write (reported as failed) when it
changed.

Refs: B-22
