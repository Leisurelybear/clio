fix(env): read .env with utf-8-sig in /api/env

A .env saved by Windows Notepad starts with a UTF-8 BOM; reading it as
utf-8 glued U+FEFF to the first key, so GET /api/env did not mask it and
PUT could not restore its masked value (the first secret got lost).
The env routes now read .env/.env.example with utf-8-sig; writes stay
BOM-free. Loader/DotenvStore are fixed by the owning group.

Refs: C-06
