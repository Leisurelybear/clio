# Clio Review Iteration Summary (2026-10-21)

- **Date:** 2026-10-21
- **Baseline:** `51ef4d0`
- **Branch:** `main` (local commits only; not pushed)
- **Preceding review:** [`2026-08-16-full-project-code-review.md`](2026-08-16-full-project-code-review.md)

## 1. Outcome

This iteration closed the highest-impact reliability gaps found in the full-project review.
Sixteen focused commits cover ASR engine consistency, cloud ASR retry/cancellation, pipeline
and plan failure semantics, Task Center project-artifact serialization, engine-aware UI
guidance, Node toolchain consistency, and desktop-test isolation.

The normal user path is unchanged:

```text
select project + videos -> compress -> analyze -> transcribe/voiceover
  -> plan -> cut/export
```

No CLI command or artifact path changed. The only transcript format change is the optional
`engine` field; legacy transcripts without it are treated as `local`.

## 2. Delivered Changes

| Area | Commits | Result |
|---|---|---|
| ASR readiness contract | `136e03f` | Readiness, doctor, batch transcription, and the Whisper check API now validate the selected engine instead of always probing the local Whisper stack. |
| Transcript engine identity | `92042ee`, `b1e9c7c` | Batch and single-file transcripts record `engine`; changing engines invalidates skip-existing results and triggers re-transcription. |
| Cloud ASR reliability | `3fd23e2`, `700ee2b` | DashScope/OSS calls share bounded retry for transient failures, while cancellation is checked during upload, submit, retry backoff, polling, and result download. |
| Pipeline failure propagation | `2c4b01f` | Full pipeline runs now return failure when any step fails; the CLI can no longer report success after a partial or empty transcription result. |
| Plan integrity | `443312a`, `af1a72e` | Plan generation rejects unusable analysis inputs and empty AI-generated plans instead of writing a misleading plan. |
| Project artifact serialization | `88ef464` | `PIPELINE`, `RERUN`, `CUT_EXPORT`, and `EXPORT` share a project-scoped lock, preventing overlapping writers in the same output tree. |
| Engine-aware UI guidance | `a4a6b6c` | Transcript panel, run panel, video menu, empty states, and config UI show local-model or cloud-credential guidance for the active engine. |
| Node toolchain | `cb7b24a` | `package.json`, doctor checks, and docs now require Node `>=22.12.0` consistently. |
| Desktop test isolation | `51ef4d0` | `pytest` refuses to launch a real pywebview window unless tests explicitly opt in or inject a fake `webview` module. |
| Roadmap reconciliation | `3fc6202` | R-047 now reflects the delivered Aliyun scope and the deferred Baidu follow-up. |
| Retry safety | `c6a9594` | Pipeline tasks using ephemeral context/prompts are marked non-retryable; the manager rejects retry and the UI explains why. |
| Retry timing | `f87d0e5` | Cloud ASR honors `Retry-After` seconds/HTTP dates, capped by the configured maximum delay. |
| Cloud error detail | `08adaa8` | DashScope per-file `subtask_status=FAILED` surfaces the provider message/code instead of a missing-URL error. |

## 3. Key Decisions

- Cloud ASR uses provider ids directly as `whisper.engine`: `local` or `aliyun`. The deprecated
  generic `cloud` value is migrated by the config loader.
- Engine readiness is capability-specific. Local checks only `faster-whisper`/cuBLAS; cloud
  checks only its own credentials. A cloud-only installation is not blocked by local CUDA setup.
- A transcript's `engine` participates in the `skip_existing` decision. Legacy transcripts
  without `engine` remain valid and are interpreted as `local`.
- Project artifact writers use one coarse lock. Different projects can still run concurrently;
  same-project pipeline, rerun, cut, and export operations serialize until writer ownership
  becomes granular.
- `pytest` must never open a real desktop window by default.
  `CLIO_ALLOW_DESKTOP_UNDER_PYTEST=1` is reserved for deliberate GUI tests.

## 4. Verification Snapshot

| Check | Result | Notes |
|---|---|---|
| `python -m pytest clio/tests/ -q --disable-warnings` | `2047 passed, 12 skipped` in 69.95s | Local Python 3.10.6; project declares Python 3.11+ |
| `npm test -- --run` | `52 files passed, 534 tests passed` | Vitest |
| `ruff check clio main.py` | Passed | No lint findings |
| `ruff format --check clio main.py` | Passed | 232 files already formatted |
| `mypy --check-untyped-defs --show-error-codes` | Passed | 38 configured source files |
| Desktop-focused tests | `43 passed` | Includes the real-webview guard |
| Process check after the suite | No residual Clio/WebView processes | Prevents orphan windows after tests |

The Python 3.11/3.12 CI matrix was not run because this batch has not been pushed. Local
Python 3.10.6 verification is useful regression evidence but is not a substitute for CI.

## 5. Operational Findings

### Multiple Clio Windows and Token Prompts

Several leftover `pytest` roots from an earlier run had each spawned a real pywebview window.
Those roots were terminated, and `51ef4d0` now fails fast under pytest before the server or
window starts.

The token prompt itself is expected:

- the desktop shell opens `/?token=...` automatically and stores the token in `sessionStorage`;
- a bare `http://127.0.0.1:<port>/` URL has no token and must show the prompt;
- `python main.py serve` prints both `API Token:` and `Token URL:` on startup.

## 6. Recommended Next Iteration

| Priority | Item | Why |
|---|---|---|
| P1 | Run the Python 3.11/3.12 CI matrix after an approved push | Local Python 3.10.6 cannot validate the declared runtime matrix. |
| P1 | Add endpoint-level HTTP/SSE lifecycle tests | Cover restart-retry, subscription after task creation, reconnect/replay, multi-tab cancel, and terminal-event races using the real server boundary. |
| P2 | Decompose the remaining large frontend modules and expand mypy scope | Reduce change coupling and legacy type debt without changing the no-build ES-module architecture. |
| P2 | Add opt-in desktop OS notifications while backgrounded | The persistent inbox is complete; native delivery remains optional. |
| P3 | Add Baidu ASR only if users need a second cloud vendor | The provider registry already supports additive providers; there is no delivery dependency on it. |

Keep the project-artifact lock coarse until plan/media writers have explicit ownership rules.
Splitting it earlier would trade a rare queueing delay for a harder-to-debug write race.

## 7. Commit Inventory

```text
136e03f fix(asr): make transcription checks engine-aware
92042ee fix(asr): record engine in single-file transcripts
b1e9c7c fix(asr): retranscribe transcripts after engine changes
2c4b01f fix(pipeline): propagate step failures through full runs
443312a fix(plan): fail when analysis inputs are unusable
af1a72e fix(plan): reject empty generated plans
3fd23e2 fix(asr): retry transient DashScope failures
700ee2b fix(asr): propagate cancellation through cloud transcription
88ef464 fix(task-center): serialize project artifact writers
a4a6b6c fix(ui): make transcription guidance engine-aware
cb7b24a chore(node): require Node 22.12 or newer
3fc6202 docs(roadmap): reconcile cloud ASR delivery status
51ef4d0 fix(desktop): block real webview under pytest
c6a9594 fix(task-center): block retry for ephemeral pipeline inputs
f87d0e5 fix(asr): honor Retry-After on transient failures
08adaa8 fix(asr): surface DashScope subtask failures
```
