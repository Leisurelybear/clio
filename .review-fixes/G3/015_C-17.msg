fix(scripts): use the default web port in serve.ps1

serve.ps1 hard-coded --port 8800 while serve.sh, the CLI default and the
desktop is_web_running() probe all use 8765. A Web UI started from serve.ps1
was invisible to the desktop app, so both servers could run on the same
task-center.sqlite3 and projects.json. serve.ps1 now uses the default port;
--port can still be passed through @args.

Refs: C-17
