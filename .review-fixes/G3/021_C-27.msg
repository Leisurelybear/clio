fix(hooks): handle non-ASCII and renamed files in pre-commit

git diff --name-only without -z quotes non-ASCII paths under the default
core.quotepath, so ruff got non-existent names; --diff-filter=ACM skipped
renamed-and-edited files; a missing venv/ruff exited 0 silently, which is how
format drift reached CI; and git add failures were ignored. The hook now
uses -z with ACMR, falls back to ruff on PATH, warns loudly on stderr when
it must skip, and fails when re-staging fails.

Refs: C-27
