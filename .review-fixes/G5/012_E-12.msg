fix(plan): URL-encode the day label in /api/plan requests

Plan save and three plan loads built `/api/plan?day=${day}` without
encoding. A day label (from ?day= or a plan name) containing &, #, + or
spaces truncated the query or injected extra params, causing 404s or a
save to the wrong day. Encode it with encodeURIComponent like the delete
path already does; add a save-URL regression test.

Refs: E-12
