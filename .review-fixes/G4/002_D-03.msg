fix(transcripts): hold path lock across revision check and write

PUT/POST /api/transcripts read the file and checked _rev without a lock and
only locked inside _save_atomic. Two tabs saving with the same revision both
passed the check, both got rev+1, and the first edit was silently lost.
The read-check-modify-write now runs under path_write_lock(tp), so the
second writer sees the bumped revision and gets 409. Adds a race test.

Refs: D-03
