fix(desktop): take over a stale lock whose port hosts another server

focus_first_instance treated every HTTPError as "first instance alive". After
a crash left clio.lock behind and its random port was reused by another local
HTTP service, every new launch printed "already running" and exited for good.
404/405/501 replies without Clio's JSON body now count as a stale lock; Clio
errors such as 503 while the window starts still mean the instance is alive.

Refs: C-16
