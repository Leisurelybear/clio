fix(transcript): track revision after segment edit or delete

Segment edit/delete succeeded but never stored the new revision from the
response, so the second write on the same video always hit 409, refetched
and re-rendered, discarding the user's second edit. Store r.revision after
both PUTs and keep the server-normalized (stripped) text locally.
Add a regression test for consecutive edits and delete-then-edit.

Refs: E-02
