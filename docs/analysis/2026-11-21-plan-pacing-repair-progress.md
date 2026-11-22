# Plan Pacing Repair Progress (2026-11-21)

Target: prevent automatic daily-vlog plans from collapsing into 5-6 second fragments.
Acceptance: 180s target, 144s floor, narrative segments >=8s, hooks/B-roll exempt.

## Checklist

- [x] Baseline audit: scope relevant diffs and exclude unrelated alias/price changes
- [x] Step 1: Prompt constraints and examples
- [x] Step 2: Runtime pacing detection and repair
- [x] Step 3: Target-aware readiness warnings in CLI/Web
- [ ] Step 4: Italy project configuration (static check done; production-loader run pending)
- [ ] Step 5: Locales, prompt regressions, and behavioral tests
- [ ] Step 6: Full verification and final diff review

## Evidence

- Baseline focused tests: 333 passed across plan, readiness, routes, analyze, and main.
- Locale key parity: zh-CN and en both contain 969 keys.
- Current worktree also contains unrelated alias/model-price changes; this task does not stage or modify them.

## Step log

- 2026-11-21: Baseline audit started.
- 2026-11-21: Step 1 completed. Replaced short 8s prompt examples with a 40s complete-beat example; added prompt regressions for the 15-40s narrative range, 80% floor, 8s minimum, 5-8 beats, and no-fragment rule. Evidence: test_prompts.py + test_prompt_overrides.py = 48 passed; git diff --check clean.
- 2026-11-21: Step 2 completed. Tightened fragment detection to 3-8s so legitimate 1-3s B-roll is not flagged; kept 80% total floor, exact-floor acceptance, one-shot repair with validation fallback, and cancel/interrupt propagation. Evidence: 8 pacing-focused tests passed; boundary audit confirms 12x5s is flagged, 7x25s passes, short B-roll passes.
- 2026-11-21: Step 3 completed. Added `clio/plan_pacing.py` as the shared rule module (80% ratio, 8s minimum, 3s B-roll cap, 30s readiness floor); `_plan_pacing_issues` and `_append_duration_window_warnings` both reuse it; five production call sites pass `cfg.plan.target_duration_sec`; added `duration_fragments` readiness warning and `{minimum}` in `duration_short`. Evidence: test_plan_pacing.py + test_plan_readiness.py + test_plan.py + test_routes_plan.py + test_main.py = 247 passed; test_i18n.py = 22 passed.
- 2026-11-22: Step 4 static check. `D:\vlog_projects\202610_Italy\project.yaml` parses with `plan.max_clips_per_day=12`, `plan.max_segments=12` (was 40 in `project.yaml.bak.pacing`), `plan.target_duration_sec=180`, `whisper.max_segments_per_clip=5`; all keys exist on `PlanConfig`/`ProjectWhisperConfig` and satisfy `_validate_config` minimums (>=1). Production loader is `clio.config.loader.load_project_config(project_dir)` / `load_config(config_path, project_dir)`; running it is still pending (the agent sandbox cannot execute repository code). Reference plans re-checked with the shared thresholds: bad plan (12 segments, 68.0s) -> total_short + fragments at positions 2-12; good plan (7 segments, 197.0s) -> no issues. Locale parity re-checked: zh-CN 970 / en 970, identical key sets.
