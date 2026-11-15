# Credential Management Code Review

**Date:** 2026-11-15

**Scope:** `86d18c6` and `adf9ffa`, compared with `558696b`

**Decision:** Findings CMR-01 through CMR-05 were fixed with focused regression tests.

The remediation keeps concurrent pipeline work intact. Reproductions and regression tests use
temporary files, a memory keyring and synthetic credentials; they do not contact an AI provider or
read/write the native credential store.

## Confirmed findings

### CMR-01 — P1: Retarget validation misses implicit provider credentials

**Locations:** `clio/ai/provider_security.py:143`, `clio/credentials/service.py:102`.

The parser resolves a legacy provider with its name, allowing the service to find the synthesized
canonical credential reference. Possession validation calls `resolve_provider(provider)` without
that name. When the provider has neither `credential_ref` nor `api_key_env`, validation sees no
credential even though runtime uses its canonical keyring/environment credential.

**Reproduction:** Load an OpenAI-compatible `custom` provider with a model and URL, put a synthetic
key in account `custom_api_key`, and PUT the global configuration with only the URL changed. The
handler returns success without a reentered key or receipt. Reloading the configuration confirms
that the new destination receives the existing credential.

**Impact:** An authenticated configuration change bypasses the intended possession check and can
redirect an existing credential. Normal request authentication is still required.

**Fix direction:** Resolve the old and candidate bindings with the same provider identity used by
the runtime, including rename handling. Cover omitted legacy fields in route-level regressions.

### CMR-02 — P1: Candidate metadata can hide the old credential from validation

**Location:** `clio/ui/routes/config_routes.py:658`.

Global configuration validation builds a single credential service from the candidate document
and uses it for both old and new providers. It preserves previously managed metadata, but permits
new metadata entries for implicitly discovered credentials. Those entries can change resolution
while the old provider is being checked.

**Reproduction:** Start with `custom.api_key_env: CUSTOM_API_KEY`, no managed metadata, and a key in
keyring account `custom_api_key`. In the same global PUT, change the provider URL and introduce
`credentials.custom_api_key.env_name: OTHER_API_KEY` with the default backend account. The guard's
environment-name equality check skips the keyring lookup and treats both bindings as empty.
The save succeeds without possession proof. Runtime uses the provider's synthesized reference
and still resolves the original keyring secret at the new URL.

**Impact:** The same bypass is possible even when the old provider has its canonical environment
name. No secret needs to be included in the configuration request.

**Fix direction:** Resolve old credentials against the trusted previous document. Validate newly
introduced registry ownership and require proof for changes to existing implicit bindings.

### CMR-03 — P1: Canonical ID collisions mix unrelated provider credentials

**Location:** `clio/credentials/service.py:65`.

Synthesized IDs use the normalized environment name, which is not injective. Valid provider
names such as `my-model` and `my_model` both produce `my_model_api_key`. The second provider
overwrites the registry and legacy-inline value belonging to the first provider.

**Reproduction:** Load both names with different inline legacy keys and different HTTPS endpoints.
`load_global_config()` succeeds, but the first provider receives the second provider's key.
This differs from the old parser, which resolved each inline value independently.

**Impact:** Existing valid configurations can send a credential to the wrong provider endpoint;
configuration order determines which value wins. Distinct legacy environment bindings can also
be overwritten by discovery.

**Fix direction:** Preserve existing provider identity when synthesizing references, or reject
collisions before resolving or writing anything. Sharing must require an explicit shared reference.

### CMR-04 — P2: Migration can activate a different, stale credential

**Location:** `clio/ui/routes/credentials.py:155`.

Migration verifies the copied destination value and then unconditionally changes the source
policy to `auto`. Verification does not establish that runtime will select that destination.

**Reproduction:** Configure an explicit `source: inline` credential with a legacy value, keep a
different stale key in its keyring account, and migrate explicitly to dotenv. The route returns
success with `source: dotenv`, but its credential status reports `effective_source: keyring`.
Reloading confirms runtime uses the stale key rather than the migrated value. Explicit dotenv
policy plus a higher-priority shell value can cause the same class of change.

**Impact:** A successful migration can break connectivity or select the wrong account even
though the copied value was verified. Automatic-policy shadowing is expected; silently changing
an existing explicit policy is the defect.

**Fix direction:** Preserve the previous effective value/source semantics, or require an explicit
policy change and verify the effective value after the proposed metadata transition.

### CMR-05 — P2: A failed save can still rotate the live credential

**Location:** `clio/ui/routes/credentials.py:95`.

The credential backend is mutated before metadata is persisted. If the YAML write fails, the
route returns failure, does not restore the old backend value and does not invalidate caches.

**Reproduction:** Replace a synthesized provider credential in the memory keyring while forcing
`write_text_atomic()` to raise `PermissionError`. The response says `ok: false`, but the backend
contains the replacement and a fresh config load uses it. The configuration cache is untouched.

**Impact:** The UI reports failure after a real rotation; cached tasks can continue using one
value while fresh tasks use another. New registry entries can also leave orphaned backend values.

**Fix direction:** Make the write and metadata transition transactional with rollback, or avoid
unnecessary metadata writes for existing entries and explicitly handle partial success with
correct invalidation. Add injected-failure regressions for both backends.

## Remediation

- CMR-01/02: old and candidate bindings now resolve through separate credential services using
  the provider identity; new metadata cannot be introduced through raw global config saves.
- CMR-03: implicit synthesized references are rejected on collision; shared references require an
  explicit `credential_ref`.
- CMR-04: explicit migration keeps the selected destination as the effective source, and verifies
  the value after the policy transition.
- CMR-05: credential replacement and migration roll back the backend and metadata transition when
  persistence fails.
- Unsaved provider connection tests use the provider name when resolving implicit credentials.

## Secret audit

The two implementation commits touch 55 paths. Neither tracks `.env`, `config.yaml`, or
`project.yaml`; all three remain ignored. No real credential was found in the two commits.

The audit inspected all 7,067 reachable Git blobs across local refs (162,564,215 bytes), checked
common provider/token/private-key/credential-URL patterns, inspected generic high-entropy secret
assignments, and compared four known local secret values in memory against the reachable blobs.
No known-local-secret match was found. Current HEAD pattern matches are synthetic redaction and
URL-sanitization test fixtures.

**Historical exposure remains:** commit `4ba8c8c` on **2026-06-10** introduced a Google API-key
format value in `vlog_tool/tests/test_utils.py:65`. Commit `29b4949` later that same day explicitly
described replacing a leaked API key with a placeholder. The original blob remains reachable from
`main` and the locally recorded `origin/main` ref. Replacing current file contents did not remove
the old value from history. This is not a secret introduced by credential management.

The value is deliberately omitted from this report and tool output. Its revocation/validity was
not tested, and the remote repository was not queried. Confirm revocation or rotate it at the
issuer. If historical removal is required, coordinate a separately authorized history rewrite
covering affected refs and clones. This review does not rewrite or push history.

A heuristic scan is not proof that no unknown secret has ever existed. The supported conclusion
is: no real secret found in the reviewed commits or tracked HEAD files; a previously acknowledged key
remains in reachable history.

## Verification

- Credential/config/provider/ASR focused suites: **256 passed, 1 skipped**.
- Connection-test/doctor/ASR-readiness/access-log/request-security suites: **101 passed**.
- Analysis/consistency/transition suites: **48 passed**.
- Full frontend suite: **970 passed**; `npm run typecheck` passed.
- Credential/config/provider-scope Ruff check passed.
- Final whole-tree mypy: **298 source files passed**. An earlier invocation saw transient errors
  in concurrently edited `test_run_preview.py`; they were absent in the final check.
- Full backend suite after remediation: **3,480 passed, 12 skipped** (150.01 seconds). This validates the current
  worktree, which includes concurrent pipeline changes, rather than an isolated checkout.
- `git diff --check` passed for the review documentation and existing tracked changes.
- The five findings were separately reproduced with production entry points and synthetic data.

Native packaged applications and macOS/Linux keyring behavior were not exercised in this review.
The focused regressions cover each CMR finding. Native packaged applications and macOS/Linux
keyring behavior were not exercised in this review.
