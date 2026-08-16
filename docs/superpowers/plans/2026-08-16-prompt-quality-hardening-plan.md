# Prompt Quality Hardening Plan

> Date: 2026-08-16
> Status: Complete
> Scope: prompt contracts, AI result validation, plan grounding, cache lineage

## Goal

Improve video analysis, planning, voiceover, and refinement quality without rewriting prompt sections that already provide useful grounding. Convert important prompt-only requirements into deterministic runtime checks.

## Preserve

- Visible-evidence-only video descriptions and explicit uncertainty
- Exact source index references in plans
- Narrative role plus visual evidence in selection reasons
- Conservative refinement intent
- Low-confidence transcript guidance
- Existing prompt override and placeholder contracts

## Phase 1: Contract Hardening

- [x] Preserve all original refinement fields and ignore undeclared AI fields
- [x] Allow `_changelog` as the only reserved refinement addition
- [x] Clamp confidence values and discard malformed timeline/highlight entries
- [x] Validate planned ranges against source timelines
- [x] Enforce `max_clips_per_day` and normalize actual plan duration
- [x] Include source timeline duration context in voiceover generation
- [x] Include complete analysis inputs and transcript metadata in plan lineage
- [x] Include the voiceover template in script lineage

## Phase 2: Prompt Corrections

- [x] Replace fixed high-density slicing with event/shot-based adaptive slicing
- [x] Cap timeline entries per analysis window
- [x] Remove the first-index example that can anchor plan output
- [x] Clarify that voiceover generation does not yet know `use_timeline`
- [x] Resolve refinement structure versus `_changelog` contradictions
- [x] Restrict script style refinement to explicit project rules
- [x] Align shared trip context with analysis and voiceover responsibilities
- [x] Improve transcript overlap selection and restore chronological order

## Phase 3: Verification

- [x] Add validator, refinement, range, duration, and lineage regression tests
- [x] Run focused Python tests (`137 passed` after review hardening)
- [x] Run full backend tests (`1861 passed, 12 skipped`)
- [x] Run Ruff formatting/checks and mypy subset

## Follow-up Evaluation

Create a fixed representative video set and compare prompt/model variants on JSON validity, hallucination rate, timeline coverage, plan range validity, duration error, voiceover grounding, refinement field preservation, token usage, and latency. Structured provider output should be introduced separately because it changes shared provider behavior and requires compatibility testing across Gemini and OpenAI-compatible services.
