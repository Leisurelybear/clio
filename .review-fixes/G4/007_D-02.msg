fix(ui): always require a session token, delivered as HttpOnly cookie

Loopback binds ran with an empty token, so any local process or page could
call /api/credentials/*/reveal, PUT /api/env or /api/run/start.
run() now always generates a token; the SPA index sets clio_session
(HttpOnly; SameSite=Strict; Path=/) on loopback, or after a valid ?token= on LAN.
/api/* accepts the cookie, Bearer or legacy ?token= (the latter two are
swapped for the cookie). The token is no longer printed for local binds.

Refs: D-02
