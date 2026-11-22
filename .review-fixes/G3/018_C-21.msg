fix(packaging): require keyring in desktop builds

The macOS build script never installed keyring and the spec's
collect_submodules("keyring") only warns when it is missing, so the mac app
had no OS keyring backend and saved credentials to plaintext .env. The mac
script now installs keyring==25.7.0 like Windows/CI, the spec aborts when
keyring cannot be imported, and build-desktop.ps1 prefers .venv python.

Refs: C-21
