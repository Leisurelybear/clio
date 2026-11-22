fix(ui): accept bodyless POST and centralize body error replies

do_PUT/do_POST each repeated the same body-error mapping and used a magic
-400 for "already answered". A POST with Content-Length: 0 ran
json.loads("") and returned 400, so `curl -X POST` to /api/webhook/trigger
failed. POST now reads an empty body as {} (Content-Type is still checked
first; PUT stays strict), and both dispatchers share one reply helper
with a named sentinel. Adds request-body tests.

Refs: D-23
