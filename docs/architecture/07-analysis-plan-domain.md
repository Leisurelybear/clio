# Clio — Analysis Windows, Scripts & Plan Domain

> Series: `07` of `00-overview.md`. Covers `clio/analyze_windows.py`, the AI text/script/plan
> tasks in `clio/tasks/` (`analyze`, `scripts`, `plan`, `refine`), and the plan domain in
> `clio/plan_model.py` + `clio/plan_readiness.py`.

## 1. Responsibilities

- Turn raw (or compressed) video into a structured **analysis** (JSON), then a **text/script**
  (voiceover copy + text), then a per-day **plan** (segment list) and an aggregated **trip plan**.
- Generate these plan artifacts with deterministic lineage, `skip_existing` reuse, and strict
  JSON output contracts (`extract_json()`).
- Express the plan as a typed domain model (`Plan`/`PlanSegment`) and gate export/cut behind
  **readiness checks** (error vs warning tiers).

## 2. Module Map

| File | Responsibility |
|---|---|
| `analyze_windows.py` | `AnalyzeWindow` (`:25`), `build_analyze_windows` (`:35`, sliding geometry), `merge_window_analyses` (`:164`, overlap-band dedup), `slice_window_video` (`:261`, temp slice + 200MB retry ladder), `shift_analysis_times` (`:106`) |
| `tasks/analyze.py` | `run_analyze_all` (`:401`) — single-clip analysis and long-clip window loop; writes `{stem}.json` with `media_identity` block |
| `tasks/scripts.py` | `run_generate_scripts` (`:143`), `_process_one_script` (`:67`), `_voiceover_lineage_fingerprint` (`:25`) — analysis → voiceover JSON |
| `tasks/plan.py` | `run_plan_vlog` (`:126`), `run_plan_all_days` (`:312`) — texts+transcripts → `{day}_plan.json` / `trip_plan.json` + markdown |
| `tasks/refine.py` | `run_refine_texts` (`:48`), `run_refine_scripts` (`:108`) — batch / `--fix` single-file refinement with `_changelog` |
| `plan_model.py` | `Plan` (`:84`) / `PlanSegment` (`:41`) typed model, `reorder`/`remove_at`, `validate_for_save` (`:150`) |
| `plan_readiness.py` | `check_plan_export_readiness` (`:72`), `ReadinessResult` (`:18`), `expand_index_keys` (`:34`), `collect_project_indices` (`:186`), `readiness_block_payload` (`:268`) |

## 3. Logical Analyze Windows (`analyze_windows.py`)

Physical splitting is **deprecated** (R-037/038); long clips are analyzed as sliding windows
*inside* `analyze` only — no physical files are committed to the project.

- Geometry: `window_max = window_max_min × 60`, `step = window_max − overlap_sec`; a single
  window covers clips ≤ `window_max`; > 1000 windows raises (`MAX_ANALYZE_WINDOWS`).
- Per window: a **temp slice** is ffmpeg-extracted (`slice_window_video`) and uploaded to Gemini
  for video analysis; the merge produces `analyze_windows` metadata inside the analysis JSON
  (`{i, start_sec, end_sec, overlap_sec, status}`).
- `merge_window_analyses`: sleeps time-sorted timeline items, keeps the **longer** text when
  near-duplicates fall inside adjacent-window **overlap bands** (dedup is *not* global — items in
  non-overlapping windows never collide, even if text repeats).
- `shift_analysis_times` / `_format_ts` preserve the original timestamp format (`HH:MM:SS` vs
  seconds) so merged output reads naturally.

## 4. Analysis → Text/Script → Plan Pipeline

```text
run_analyze_all            ({stem}.json: title/summary/highlights/timeline/location/text,
                             media_identity, analyze_windows)
   └─ run_generate_scripts ({stem}_voiceover.json: text + media_identity)   [scripts.py]
   └─ run_plan_vlog        ({DayLabel}_plan.json + _plan.md)                [plan.py]
        source set: texts (voiceover) + transcripts (ASR)
        per segment: index/title/reason/use_timeline/voiceover_hint/subtitle
   └─ run_plan_all_days    trip_plan.json (per-day + overall summary)       [plan.py]
```

- **skip_existing** is inherited from the `analyze` toggle: `run_plan_vlog` reuses an existing
  plan JSON when files are unchanged and history exists (`_plan_lineage_fingerprint`, plan.py:43).
- Plan day discovery reuses **text stems** (`_discover_day_labels`), and default segment clips
  come from `source_file`/`videos.json` ordering — independent of cut/export phase.
- Refinement (`--fix`) targets a **single file** (required `-i`), emits a `_changelog`, and the
  first entry must state `Modified XXX per user feedback` (prompt contract).

## 5. Plan Domain (`plan_model.py`)

- `Plan`: `day_title`, `theme`, `total_estimated_sec`, `opening_tip`, `ending_tip`, `sequence`,
  `confidence` (accepts `_confidence`/`confidence`), plus round-trip `extras` for unknown keys.
- `PlanSegment`: `index`, `title`, `reason`, `use_timeline`, `voiceover_hint`, `subtitle`;
  unknown keys preserved in `extras` (forward-compatible with prompt output).
- `validate_for_save`: save-time **hard errors** only — empty `index` and syntactically invalid
  `use_timeline` (`parse_time_range`). Structural mutation (`reorder`/`remove_at`) keeps the
  model in sync with the UI drag/drop editors.
- UI save path (see `10-ui-frontend.md`) serializes the edited plan, runs `validate_for_save`,
  then `check_plan_export_readiness` before writing `{day}_plan.json`.

## 6. Readiness Checks (`plan_readiness.py`)

**Error tier** (block export/cut):

| code | trigger |
|---|---|
| `sequence_empty` | plan sequence is empty |
| `index_empty` | segment has no index |
| `index_missing` | index is not in `known_indices` (**only when discovery ran** — a `None` discovery never guesses) |
| `timeline_invalid` | `use_timeline` fails `parse_time_range` |

**Warning tier** (`force:true` bypasses; otherwise the caller blocks with `needs_force`):

| code | trigger |
|---|---|
| `video_offline` | referenced index maps to an original in `videos.json` that is missing on disk |
| `timeline_empty` / `title_empty` / `reason_empty` | missing optional fields |
| `duration_short` / `duration_long` | `total_estimated_sec` < 30 or > 1800 |

- **Index normalization**: `expand_index_keys("1") ≡ expand_index_keys("001")` — plan sequences
  store zero-padded indices (`format_index`) while texts/ASR often store ints; exact-string
  matching between the two forms historically produced false `index_missing` errors.
- `collect_project_indices` derives known/offline sets from compressed stems **and** texts
  (`media_identity` + `source_file`/`compressed_file`), then cross-maps offline originals via
  `load_selected_videos`.
- `readiness_block_payload` centralizes the HTTP 400 response for export/cut endpoints.

## 7. Cross-Module Dependencies

- → `clio/prompts.py` (`_wrap_with_context` injected trip context; JSON output contract)
- → `clio/ai/` (provider registry — `refine_text` may bind DeepSeek; video tasks Gemini)
- → `clio/analyze.py` (`extract_json`, `_analyze_clip`), `clio/utils` (atomic writes,
  `format_index`, `safe_basename`)
- → `clio/identity` + `clio/vmeta` (`media_identity` embedding, legacy offset gate)
- → `clio/cut` (`parse_time_range` shared with cut/export validation)
- → `clio/task_center` (plan/refine run as managed tasks)
- Consumed by: export (readiness gate), cut, Plan Editor UI, trip summary, verify.

## 8. Risks / Maintenance Notes

1. `merge_window_analyses` de-dups by `SequenceMatcher` text similarity + ≤5s start gap, but the
   first window is used as the base (`base` dict); per-window high-level fields (title,
   `brief_points`, etc.) that only exist in later windows are **dropped** unless merged
   explicitly — the merge is timeline-centric by design (P2-era windowing bugs).
2. Plan `index` normalization survives three representations (str/int/padded), but `expand_index_keys`
   regenerated in three places (`index.py`, `plan_readiness.py`, UI) — a shared frontend/backend
   contract change must hit all three.
3. `run_plan_vlog` skip-existing reuses the JSON when *lineage matches*; a source analysis change
   without a fingerprint change can go unnoticed (fingerprint covers file hashes, not contents).
4. `collect_project_indices.offline` falls back to filename stems; after a relink the offline set
   can lag until reindex runs (identity first, stems second — see `05-media-identity.md`).
5. `--fix` writes *only* `_changelog` in the refined JSON; the summary/docs expect the first entry
   to follow the `Modified XXX per user feedback` template — enforcement is prompt-level, not
   structural.