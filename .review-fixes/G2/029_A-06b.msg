fix(tasks): read user-edited JSON with utf-8-sig

Plans, analyses, voiceovers and transcripts saved by Notepad on Windows start
with a UTF-8 BOM; json.loads(read_text("utf-8")) then fails, so cut/export
crash and several scanners silently skip the file.
Switch those reads in clio/export and clio/tasks to utf-8-sig (refine's bytes
decode too). Writes, internal caches and lock files are unchanged.

Refs: A-06b
