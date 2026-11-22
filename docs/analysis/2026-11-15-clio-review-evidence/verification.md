# CLIO Review Verification Record (2026-11-15)

## Baseline and environment

- Repository baseline: `aef5b0a96692a93893f680158a30044dfc867e53`.
- Initial baseline: `781ae0e`; existing runner work was subsequently committed by another process.
  The four intervening commits were inspected. No production backend changed in that interval.
- Local execution: Windows, Python 3.11.9 in `.venv`, Node 24.18.0; date/timezone:
  November 15, 2026, Asia/Shanghai.
- Review scope: local repository and offline fixtures. No external documentation or advisory
  database research was needed for the reported source-code defects.
- No production source was changed by the review. Existing untracked patches, artwork and the
  prior UX mockup were preserved. Temporary files used by reproductions are automatically removed.

## Executed checks

| Command | Result | Notes |
| --- | --- | --- |
| `.venv/Scripts/python.exe -m pytest clio/tests/ -v --junitxml=.pytest_cache/review-2026-11-15.xml` | 3,409 passed; 12 skipped; 137.78 s | Full backend suite, before the final test-only preview addition |
| `.venv/Scripts/python.exe -m pytest clio/tests/ --cov=clio --cov-branch --cov-report=term-missing --cov-report=json:.pytest_cache/review-2026-11-15-python-coverage.json` | 3,409 passed; 12 skipped; 121.57 s | Coverage instrumentation; combined 86.8168% |
| `.venv/Scripts/python.exe -m pytest clio/tests/test_run_preview.py -q` | 70 passed; 20.16 s | Final-baseline preview tests, including newly added upstream-cache case |
| `npm run test:coverage` | 951 passed; 90 files; 21.56 s | Final baseline; statement 61.04%, branch 55.19%, function 65.38%, line 63.93% |
| `.venv/Scripts/python.exe -m ruff check clio main.py` | Passed | Rechecked on final baseline |
| `.venv/Scripts/python.exe -m ruff format --check clio main.py` | Passed; 287 files already formatted | Check mode; no formatting mutation |
| `.venv/Scripts/python.exe -m mypy --check-untyped-defs --show-error-codes` | Passed; 287 source files | Existing project settings, including `strict=false` |
| `npm run typecheck` | Passed | Existing explicit 10-entry include list; imports can expand the graph |
| `.venv/Scripts/python.exe main.py check` | Exit 0; 8 `[OK]` markers | Captured without publishing local config/path details; not live inference |
| `.venv/Scripts/python.exe docs/analysis/2026-11-15-clio-review-evidence/reproduce.py` | 13 observations reproduced | Current defects, IDs listed below |
| `npx vitest run --config docs/analysis/2026-11-15-clio-review-evidence/vitest.config.js` | 4 passed | Delayed-promise/state probes; current defective behavior |
| Evidence-script Ruff check / format check | Passed; 3 scripts formatted | Applies only to the new audit tooling |
| Matrix/links/whitespace validation and `git diff --check` | Passed | 27 JSON/CSV rows agree; source locations and local document targets exist |

The first two full Python executions contain the same 3,409 passing tests; they are not 6,818
distinct cases. The later 70-case run is a focused rerun and must not be added to the full-suite
count. A fresh full Python suite after the test-only addition was not needed: unchanged backend
production plus the affected final-baseline test module were checked. Likewise, reproduction
cases are separate from the normal suite and should not inflate its result count.

Coverage records 23,792 Python statements (21,201 covered) and 8,408 branches (6,754 covered),
giving 89.11% statement, 80.33% branch and 86.82% combined coverage. Frontend coverage records
10,788 statements (6,585 covered) and 8,637 branches (4,767 covered). Different language tools
use different coverage models; the percentages are not directly comparable quality scores.

Eleven Python skips concern POSIX permission behavior on Windows. One `test_server` static test
skips because obsolete `app.js` is absent. Current ES-module serving is not validated by that
skipped test. Real FFmpeg smoke was not among the skipped tests. No skipped case is reported as
passing.

Local raw suite logs remain under ignored `.pytest_cache/review-2026-11-15-*`; frontend coverage
also resides in ignored `coverage/`. They are not committed artifacts and can be regenerated.
Portable aggregate evidence is in [measurements.json](measurements.json), with the observed
Python results in [reproduction-results.json](reproduction-results.json). No raw local
configuration or credentials are archived in these reports.

## Reproduction semantics

These probes deliberately **assert the observed defect**. A passing probe means the problem is
present. They are audit evidence outside normal pytest/Vitest discovery, not production regression
tests with desired behavior. When fixing an issue, translate the scenario into an ordinary test
that asserts the desired result. Do not weaken a fix merely to keep these defect assertions green.

| Finding IDs | Probe / method | Observation |
| --- | --- | --- |
| R01 | `reproduce.py` | Preview skips a compressed artifact rejected by runtime settings reuse |
| R02 | `reproduce.py` | Codec/CRF mutation does not change compression fingerprint/match |
| R03 | `reproduce.py` | Real versioned manual edit inside fake AI callback is replaced on completion |
| R04 | `frontend.test.js` | Late A refinement replaces B's shared editor buffer |
| R05 | `frontend.test.js` | Earlier day1 response overwrites later day2 selection |
| R06 | `frontend.test.js` | Save completion clears dirty for an unsaved later edit |
| R07 | `reproduce.py` plus source inspection | Custom text/script files exist but Web resolver returns None |
| R08 | `reproduce.py` | Web resolver accepts an outside-root symlink rejected by containment helper |
| R09 | `reproduce.py` | Three-file draft bundle is reduced to content-only auto-copy |
| R10 | `reproduce.py` | Repeated auto-copy replaces edited destination content without backup |
| R11 | `reproduce.py` | Cache clear closes fake provider inside an active provider_use lease |
| R12 | `reproduce.py` | Two independent successful usage writers retain one persisted call |
| R13 | `reproduce.py`, also R03 | Plan JSON save leaves old Markdown; refine does not create TXT |
| R14 | `reproduce.py` | Keyless Ollama binding fails check while other diagnostics are patched healthy |
| R15 | `reproduce.py` | Actual compression index writer overwrites same-stem source index |
| R16 | `reproduce.py` | Equal-size source edits at .1/.9 seconds remain fresh |
| R17 | `frontend.test.js` plus source inspection | Player URL omits selected directory; backend name ambiguity yields 409 |

Python probes use temporary files and patch external provider/probe boundaries. They exercise
real route functions, metadata/reuse helpers, persistence and accounting. Frontend probes use
real selection/save/refine functions with jsdom state and mocked API/import boundaries. They
do not establish complete browser interactions, live transport behavior or GUI import results.

Symlink creation succeeded in this Windows environment. The script explicitly reports SKIPPED
if rerun where symlink permissions are unavailable; treat that as missing evidence, not a pass.
R12's barrier forces an actual possible interleaving rather than relying on a timing sleep.

## Rebuilding portable evidence

```powershell
.venv/Scripts/python.exe docs/analysis/2026-11-15-clio-review-evidence/reproduce.py
npx vitest run --config docs/analysis/2026-11-15-clio-review-evidence/vitest.config.js
.venv/Scripts/python.exe docs/analysis/2026-11-15-clio-review-evidence/inventory.py
.venv/Scripts/python.exe docs/analysis/2026-11-15-clio-review-evidence/render-matrix.py
git diff --check
```

`inventory.py` reads tracked files and existing Python/frontend coverage summaries. Run the
coverage commands first if those ignored outputs were removed. It generates file inventory and
measurements, not manual inspection claims. The explicitly listed focused files represent
selected sections, not a promise of every line read. Snapshot hashes reflect inventory time
and may differ after subsequent documentation updates or another working process's edits.

`render-matrix.py` validates unique finding IDs, source-path existence and source line bounds,
then renders Markdown/CSV from `findings.json`. Matrix links use source line anchors valid for
this baseline; future edits may move those lines. CSV is UTF-8 with BOM for common spreadsheet
applications. Manually changing a rendered finding without updating JSON will be overwritten.

## Unexecuted acceptance boundaries

No live billable AI/provider or cloud ASR calls, model-quality corpus, actual browser media
playback/accessibility session, native desktop dialogs, actual JianYing import, new package
build, hosted GitHub Actions run, fresh dependency advisory scan, external penetration test,
cross-platform manual pass, crash-injection campaign or large-project capacity benchmark was
performed. Historical green CI in earlier reports is historical evidence only. See O03-O05/O10
for concrete follow-up acceptance work.

The review added reports/evidence and updated roadmap/tracker links. It performed no business-code
repair, local commit, push, release or destructive workspace cleanup.
