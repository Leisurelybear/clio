# Credential Management Architecture Review

**Date:** 2026-11-15
**Review type:** Design and architecture review
**Related design:** [`2026-11-15-credential-management-design.md`](../specs/2026-11-15-credential-management-design.md)

## Executive assessment

The current credential boundary has useful defensive pieces, but it is not a coherent credential
architecture. It combines a global provider registry, process environment resolution, a masked raw
`.env` editor, and provider-specific UI code. The user-facing problem is structural: the UI edits a
storage format instead of managing a credential.

Introduce a `CredentialService` and make runtime consumers and UI operations use it. Keep `.env` as
a compatibility adapter and add an operating-system keyring adapter for desktop installations. Do
not replace the existing config layering or invent application-owned encryption.

## Current architecture map

| Concern | Current implementation | Review result |
|---|---|---|
| Global ownership | Provider registry belongs to `config.yaml`; project config contains task bindings | Strong boundary; preserve it |
| Secret reference | `api_key_env`, with optional inline `api_key` legacy field | Works, but leaks storage details into config/UI |
| Runtime resolution | `clio/config/parsers.py` reads `os.environ`, then inline value | Centralize behind one service |
| Secret storage | Global `.env`; process environment also participates | Compatible, but precedence is implicit |
| Raw file UI | `GET/PUT /api/env` masks known keys and edits the complete file | Keep as compatibility tool; wrong primary UX |
| Provider UI | Provider modal writes `.env`; card “show key” displays a placeholder | Broken interaction contract |
| Provider API | CRUD endpoints mask/strip keys and enforce canonical env names | Good controls; simplify around references |
| Security | Local-session/origin checks, API token, atomic dotenv writes, dangerous env blocking, log redaction | Good foundation |
| Rotation | Config/env saves clear provider cache immediately | Conflicts with active calls; R11 must be fixed |
| Keyless local model | Ollama is allowed without a key in provider and doctor code | Preserve as a typed credential policy |

## Strengths to retain

1. Global/project separation keeps provider definitions and credentials global while project files
   contain task bindings.
2. `provider_security.py` derives canonical `<PROVIDER>_API_KEY` names and requires possession proof
   when a provider URL or credential target changes.
3. The dotenv route masks known secret names, restores unchanged masks, rejects dangerous
   executable/module-injection variables, uses an atomic writer, and reloads after saving.
4. The server already has local-session, origin, API-token, and access-log privacy controls.
5. The provider factory is an existing lifecycle boundary that can consume resolved credentials.

## Findings

### CRED-01 — The UI edits a storage document instead of a credential

**Severity:** High usability, medium security risk
**Evidence:** `editor-providers.js` calls `GET /api/env`, searches for a provider-generated
environment name, and sends the whole file to `PUT /api/env`.

This couples Provider editing to dotenv syntax, makes the browser responsible for preserving
unrelated entries, and creates last-write-wins behavior for multiple tabs or tools. It also makes
non-environment credentials difficult to add.

**Recommendation:** field-level credential operations through a service and dedicated API. Retain
`/api/env` for advanced non-secret environment editing.

### CRED-02 — The Provider reveal control is functionally false

**Severity:** High usability
**Evidence:** the click handler replaces the value with localized `key_not_exposed` text; it never
requests or displays the configured key.

**Recommendation:** show status by default and make full reveal a separate authenticated POST with
confirmation, no-store headers, and a short UI lifetime.

### CRED-03 — Secret source precedence is implicit

**Severity:** Medium architecture risk
**Evidence:** the parser checks `os.environ` before inline config; normal dotenv loading avoids
replacing existing process values, while UI save reloads dotenv with `override=True` and updates
`os.environ`.

The same process can therefore contain inherited values, dotenv values, and UI-reloaded values with
lifecycle-dependent precedence. Users cannot tell why a newly saved key is not used.

**Recommendation:** expose effective and shadowed sources. Preserve inherited environment precedence
for deployment compatibility without silently overwriting it from a UI save.

### CRED-04 — No store abstraction exists

**Severity:** High extensibility risk
**Evidence:** provider parsing, doctor checks, ASR readiness, env routes, and Provider UI all know
about environment-variable names or dotenv behavior.

**Recommendation:** add `CredentialService` with Environment, Keyring, Dotenv, and legacy-inline
adapters. Make it the sole runtime read path.

### CRED-05 — Rotation and provider cache invalidation are coupled unsafely

**Severity:** High runtime correctness risk
**Evidence:** `/api/env` and config writes call `_clear_provider_cache()`, which closes cached
providers immediately. Review finding R11 records that this can interrupt active AI requests.

**Recommendation:** defer closure of retired providers until `active_calls == 0`, and invalidate
only providers affected by the changed credential.

### CRED-06 — No generic inventory or consumer relationship exists

**Severity:** Medium product/architecture risk
**Evidence:** the durable relationship is a provider environment-variable name; cloud ASR uses
separate readiness logic.

**Recommendation:** add generic credential metadata and derive consumer references from Provider and
ASR configuration instead of duplicating consumer lists in YAML.

### CRED-07 — The raw `.env` editor must remain, but cannot be the primary path

**Severity:** Medium compatibility risk
**Evidence:** `.env` also contains safe non-secret values such as language, proxy, and tuning
settings.

**Recommendation:** keep it as an advanced compatibility screen, mark managed entries as owned by
Credential Manager, and link back to the credential panel.

### CRED-08 — Tests cover file behavior, not the cross-layer contract

**Severity:** Medium verification gap
**Evidence:** route tests cover masking, blocked variables, atomic writes, and retargeting, but no
service contract spans parser, doctor, provider factory, ASR, API metadata, reveal, and cache life.

**Recommendation:** add behavioral tests at the service boundary and integration tests for runtime
consumers, including source shadowing and missing keyring behavior.

## Boundary decisions

Keep `config.yaml` global for credential metadata and Provider definitions. Keep `project.yaml`
limited to task/provider/model bindings. A project references a global credential through its
Provider; it never receives the secret or a writable credential store.

Use the OS credential manager for desktop secrets, with dotenv and process environment adapters for
compatibility and deployment. Do not encrypt a file with a key stored beside that file.

Metadata routes may return status. Secret values only cross the API in explicit write, reveal, or
ephemeral connection-test requests. Secrets never appear in paths, query strings, task metadata,
notifications, or logs.

Providers and ASR receive resolved values from one service. Ollama's exception is expressed as a
credential requirement policy.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Keyring unavailable on headless machine | Detect capability; use dotenv fallback and report source |
| UI edits expected to override shell environment | Display “shadowed by process environment”; require explicit source |
| Reveal increases exposure | POST, normal auth, confirmation, no-store, timeout, redacted logs |
| Provider rename changes ownership | Stable `credential_ref`; explicit reassignment and proof |
| Migration loses an old key | Copy first, verify, then offer cleanup; never delete automatically |
| Rotation breaks a running job | Deferred provider retirement and active-call leases |
| Generic registry lacks validation | Restrict kinds and enforce ID/value size limits |

## Review decision

The design fits this repository if implementation follows the existing global/project contract and
uses small commits. The first production milestone should be the service contract and dotenv-backed
implementation, with no keyring dependency required for CI, CLI, or headless deployments. The UI
milestone can then remove the broken reveal behavior and stop Provider forms from editing the full
`.env` document. Keyring support and migration should follow after the service contract has
behavioral tests.

Resolve or explicitly schedule R11 provider cache lifetime handling before enabling hot credential
rotation.


## Implementation follow-up

CRED-01 through CRED-08 are implemented as of 2026-11-15. R11 now retires active clients
gracefully and runtime callers atomically reacquire their lease. See the
[implementation record](2026-11-15-credential-management-implementation.md) for behavior,
verification and delivery limits. The findings above preserve the original review baseline.

The subsequent [code review](2026-11-15-credential-management-code-review.md) reproduced five
implementation defects (CMR-01 through CMR-05). All five were fixed with focused regression tests;
the original CRED findings and this follow-up remain separate tracking records.
