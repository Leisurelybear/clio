# Clio — Export (JianYing Draft)

> Series: `12` of `00-overview.md`. Covers `clio/export/` — mapping a validated plan to a
> JianYing Pro (剪映) draft project that the user finishes by hand.

## 1. Responsibilities

- Translate a plan (`{day}_plan.json`) into a **JianYing 5.9 `draft_content.json`** that opens in
  CapCut/JianYing for manual finishing.
- Resolve each planned segment to an **original video** (via `videos.json` selection, text
  `media_identity`/`source_file`, then `{index}_` filename prefix fallback) and lay clips onto
  the timeline back-to-back with their `use_timeline` ranges.
- Never emit an empty/corrupt draft: refuse to generate when a non-empty plan yields zero video
  materials.

## 2. Module Map

| File | Responsibility |
|---|---|
| `jianying.py` | Core exporter: `_build_index_to_source` (`:50`, index→original stem from texts), `_build_index_to_offset` (`:92`, legacy `_seg` offsets), `_resolve_video`/`_resolve_video_by_prefix` (`:115`/`:27`), `_build_materials` (`:139`, materials.videos + caption texts), `_build_tracks` (`:231`, video/text timeline tracks), `export_plan_to_jianying` (`:323`) |
| `__init__.py` | `FORMAT_REGISTRY = {"jianying": export_plan_to_jianying}` (`:10`); `export_plan(format, …)` dispatcher (`:13`, unknown format → `ValueError`) and merges `project_dir`/legacy `input_dir` handling |

## 3. Material Resolution

```text
plan index (per segment)
  └─ index_to_source: texts/*.json  index →   media_identity.original_stem (v2)
                                        else source_file stem (v1)
  └─ index_to_offset: texts media_identity → legacy_segment_offset_sec (split only)
  └─ _resolve_video(stem, videos)      case-insensitive original_stem match
       └─ else _resolve_video_by_prefix(idx, …)   '001' → '001_GL…'
  └─ one video material per unique index: {id, type:video, path, duration(µs)}
```

- **Preferred media source**: `project_dir/videos.json` (`load_selected_videos`); legacy
  fallback scans `media_dir`/`input_dir` with `find_videos` when no `project_dir` is given.
- Text materials (captions) come **only** from `seg.subtitle` — `voiceover_hint` is narration
  preview and must not drive CapCut text tracks (P2-P51).
- Captions are stored as `content: json.dumps({text, font_color, font_size, bold})` — the exact
  shape JianYing 5.9 expects for a text material.

## 4. Timeline Assembly

```text
_Build_tracks: for each segment whose index HAS a video material:
  parse use_timeline → (start_sec, end_sec)
  offset = index_to_offset.get(idx, 0.0)
  video_segment: target_timerange{start: accumulated, duration}  ← back-to-back
                 source_timerange{start: (start_sec + offset)µs, duration}
  text_segment  (from seg.subtitle) aligned to the same target range
  accumulated += duration_µs
```

- **Back-to-back timing**: successive segments start exactly where the previous ends
  (`accumulated_us`), matching a continuous edit; the plan’s own gaps/overlaps are not honored.
- **Legacy split correction**: `source_timerange.start` adds the segment offset so a `_segNN`
  clip is read from the right absolute position (see `05-media-identity.md`).
- First come, first dropped: segments with invalid/missing `use_timeline` or no material are
  skipped and counted, but an export with a non-empty plan and **zero** resolved videos raises
  `RuntimeError` instead of writing a blank draft.

## 5. Output Document

`draft_content.json` under `output_dir`:

```json
{ "id": <uuid>, "name": plan.day_title, "duration": total_µs, "fps": 30,
  "canvas_config": CANVAS_PRESETS[ratio],        // 16:9 default, presets in config.models
  "platform": {app_source:"lv", app_version:"5.9.0", os:"windows"},
  "materials": {video/text/…}, "tracks": [video-track, text-track] }
```

Written via `write_text_atomic`. Opened in JianYing by importing the draft folder.

## 6. Cross-Module Dependencies

- → `clio/plan_readiness` (`expand_index_keys`), `clio/cut` (`parse_time_range`)
- → `clio/identity` (`load_identity`, `legacy_segment_offset_sec`)
- → `clio/config.models` (`CANVAS_PRESETS`), `clio/utils` (`get_duration_sec`, `find_videos`, `write_text_atomic`)
- → `clio/tasks/_video_loader` (`load_selected_videos`)
- Invoked from: `clio/tasks/export` (via this package), `POST /api/export`, plan editor
  `plan-export.js` (`exportJianyingDraft`)

## 7. Risks / Maintenance Notes

1. Resolve is duplicated among `file_service`, `identity.resolve_identity`, `ArtifactIndex`,
  and here (`_resolve_video*`) — each reimplements stem/`.vmeta`/`videos.json` preference order;
  keep them consistent when identity/sidecar behavior changes.
2. `_resolve_video_stem` matches `v.stem == source_stem` (case-insensitive); two originals with
  the same stem (different dirs) pick the first in scan order — legacy layout only.
3. Format is pinned to JianYing **5.9 (plain JSON)**. A future CapCut version that switches to a
  different schema or encryption breaks the draft; bump `app_version` and re-verify round-trip.
4. `total_duration_us` is derived from track segment max-end, not from the sum of durations —
  a segment with duration but a broken text-only track could misreport length; harmless while
  only video feeds the clock.
5. Canvas fix `fps: 30` and `canvas_config` come from `CANVAS_PRESETS` keyed by `canvas_ratio`;
  custom ratios not in the presets silently fall back to `16:9`.