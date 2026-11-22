feat(cli): read serve API token from file or CLIO_API_TOKEN

`serve --token` was the only CLI way to pass the API token, which leaks it
via the process list (Task Manager, wmic, ps, /proc) and shell history -
and with --host 0.0.0.0 it is the only access barrier. Add --token-file and
the CLIO_API_TOKEN environment variable (order: --token, --token-file, env,
config). --token keeps working but prints a warning.

Refs: A-14
