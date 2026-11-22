style(config): collapse triple blank lines so ruff format --check passes

models.py and validators.py had three blank lines between top-level
definitions; ruff format collapses them to two, so the quality job's
`ruff format --check` failed.

Refs: C-02
