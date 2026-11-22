fix(ui): reject cross-site API requests via Sec-Fetch-Site

The origin check let any request without Origin and Referer through, so a
cross-site <img referrerpolicy="no-referrer"> could hit GET /api/* on the
loopback server (e.g. /api/fs/entries with a UNC path -> NTLM leak).
Now every /api/* request whose Sec-Fetch-Site is present must be
same-origin or none; static assets stay public. Adds regression tests.

Refs: D-01
