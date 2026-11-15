# Credential Management Implementation

**Date:** 2026-11-15
**Status:** Implemented locally; no push or release performed

## Delivered behavior

`clio.credentials` owns metadata validation, resolution, source status and store operations.
Automatic resolution follows inherited environment, native keyring, dotenv, then legacy inline
values. Explicit source policies remain supported. Dotenv-owned process entries are tracked so
settings saves and deletions preserve inherited shell values. Provider config keeps resolved
values internally with repr redaction; cloud ASR and doctor use the same resolution policy.
Ollama's credential-free policy is shared with the provider constructor.

Global `credentials` metadata supports API keys, access keys, secrets and passwords without
persisting values, hashes or prefixes. Legacy references are synthesized in memory; config
loading does not migrate or persist them. Project config rejects credential metadata.

Authenticated credential APIs provide metadata inventory, field replacement, confirmed reveal,
selected-backend deletion and explicit migration. Reveal uses existing no-store response
headers; credential request/response bodies are excluded from access logs. Dotenv updates
preserve unrelated entries and comments, replace duplicate assignments, reject dangerous
variables and symlinks, and reuse the permission-restricted atomic writer. Config, credential
and raw dotenv mutations share a process lock. Registered dotenv entries are protected from
raw-editor changes, including stale-tab overwrites and quoted-value round trips.

Provider forms no longer read or rewrite `/api/env`. Shared fields appear on Provider cards
and in Global Settings; the inventory includes Aliyun transcription and other discovered
credentials. They show source and shadowing, replace values, reveal for 30 seconds, migrate
explicitly and select a backend for cleanup. Reveal clears on timeout, page removal and hidden
document. Provider drafts store references and a five-minute possession receipt instead of
secret values. Endpoint/name/type retargeting still requires possession of the current value.
Unsaved connection tests accept an ephemeral value without persisting it in config.

Native OS keyring is optional for CLI/headless installations and included in desktop build
dependencies and PyInstaller imports. Third-party plaintext keyring backends are excluded.
Migration copies and verifies before changing references, removes migrated inline values and
retains the legacy dotenv entry. Unavailable keyring falls back to dotenv only on explicit
migration; an explicit failed keyring replacement is reported instead of silently redirected.

Provider invalidation now retires affected cached clients. Active calls, including nested and
exceptional calls, retain their client until the last lease exits. Production call sites
reacquire under the lease lock to cover the lookup-to-call handoff. This addresses CRED-05/R11.

## Verification

- Credential/config/server/ASR/doctor/i18n focused suite: 516 passed, 2 skipped; final quoted
  dotenv and lifetime regressions: 72 passed, 1 skipped.
- Full frontend suite: 961 passed; frontend typecheck passed.
- Full Python suite during concurrent workspace changes: 3446 passed, 12 skipped, 2 failures
  outside credential delivery (`test_i18n_inventory` missing new pipeline resume keys and
  `test_run_preview::test_preview_files_selection_filters_non_plan_steps`). An earlier run
  encountered a transient missing `clio.segment_cache` import while concurrent edits landed.
- Mypy passed across 297 files; credential-scope Ruff and formatting passed. Whole-tree Ruff
  observed concurrent pipeline import-order issues; they are outside this delivery.
- Local `main.py check` passed. Native Windows keyring set/read/delete passed using a random
  temporary account, which was removed. Ordinary pytest runs isolate OS keyring access.
- `git diff --check` passed.

The desktop workflow/spec wiring was reviewed locally; no packaged binary or GitHub Actions
build was produced. macOS/Linux native keyring access was not tested on this Windows host.
The cross-tab mutation lock protects one Clio process; external programs editing the same files
still require normal coordination. Existing unrelated pipeline work and review evidence were
preserved and excluded from the credential code commits.

## Follow-up remediation

The post-implementation code review reproduced five edge cases. The follow-up fixes now validate
implicit bindings against the previous provider identity, reject implicit credential-ID collisions,
protect raw config from introducing managed metadata, preserve explicit migration source policy,
roll back backend writes when metadata persistence fails, and resolve implicit credentials in
unsaved connection tests. Focused coverage is 114 passed; the full Python suite is 3,480 passed and
12 skipped.
