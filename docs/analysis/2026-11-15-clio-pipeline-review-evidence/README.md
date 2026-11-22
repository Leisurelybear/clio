# Archived Pipeline Defect Evidence

`reproduce.py`, `runner.test.js`, and `vitest.config.js` preserve the pre-remediation
review probes from November 15, 2026. Their assertions intentionally expect defects;
they must not be used as current acceptance tests.

Current production regression coverage:

- `clio/tests/test_pipeline_recovery.py`: managed failures, persisted interruption,
  artifact reuse, cleared overwrite approvals and safe configuration identity.
- `clio/tests/test_run_preview.py`: preview/runtime compression reuse agreement.
- `clio/tests/test_processing_state.py`: shared state and delayed/concurrent flushes.
- `clio/tests/test_pipeline.py` and `test_routes_run.py`: explicit empty steps.
- `clio/ui/static/src/__tests__/runner-recovery.test.js`: task lifecycle, safe
  completion, fast retries, correct day and project switching.
- `clio/ui/static/src/__tests__/sidebar-data.test.js`: stale project list responses.

Run them through pytest/Vitest as documented in the parent review report. Full
verification and recovery limits are recorded in that report.
