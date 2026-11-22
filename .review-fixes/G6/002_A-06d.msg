fix(ui): read plan JSON with utf-8-sig in plan and export routes

A plan edited in Notepad gains a BOM; the plan and export routes then failed
with JSONDecodeError while the CLI path (A-06) already accepted it.

Refs: A-06d
