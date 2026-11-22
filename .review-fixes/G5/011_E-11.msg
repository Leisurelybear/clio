fix(ui): stop leaking the API token in media and SSE URLs

Cover <img>, player/range-picker <video> and both EventSource URLs
appended ?token=<api token>, exposing it in DOM attributes, devtools,
access/proxy logs and copied image addresses. Per the local-auth contract
the backend now sets an HttpOnly SameSite=Strict clio_session cookie, so
drop the query token from all seven call sites. fetch() still sends the
Bearer header and ?token= on the page URL is still read once.

Refs: E-11
