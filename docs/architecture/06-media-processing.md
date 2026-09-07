# Clio — Media Processing Tasks

> Series: `06` of `00-overview.md`. Covers `clio/compress.py`, `clio/cut.py`, and the media
> tasks in `clio/tasks/` (`compress`, `cut`, `label`, `cover`, `waveform`, `reindex`, `verify`).

## 1. Responsibilities

- ffmpeg-based media operations: **compress** raw footage to preview files, **cut** timeline
  segments, **label** index badges, **cover** thumbnail extraction, **waveform** peak caching,
  **reindex** sidecars, and **verify** artifact consistency.
- Every operation is a wrapper over `clio/utils.py` subprocess helpers (`run_ffmpeg`,
  `run_probe`, `run_subprocess`) with atomic outputs, cancel support, and skip-existing
  semantics inherited from the step runners.

## 2. Module Map

| File | Responsibility |
|---|---|
| `compress.py` | `compress_video` (`:40`) — bitrate-targeted preview encode (scale + fps, optional `-an`, target-byte budget), temp-file → `os.replace` atomic commit; `_get_audio_bitrate` (`:16`) |
| `tasks/compress.py` | `run_compress_all` (`:200`) — per-source cache lookup `_find_reusable` (`:88`, fingerprint + settings + `is_stale`), `_prune_stale_siblings` (stale same-source outputs), `.vmeta`/`.vindex` writes, `skip_existing`; offline sources skipped with a clear log |
| `cut.py` | `parse_time_range` (`:10`), `_to_seconds` (`:22`), `cut_one` (`:38`) — `-ss/-t` with `-c copy` (default) or `-c:v libx264 -crf 23` (reencode) |
| `tasks/cut.py` | `run_cut_all` (`:257`) — batch cut from plan segments, `CutBackupConflictError` (`:35`), day `output/cuts/<day>/` layout, source original/compressed |
| `tasks/label.py` | `build_label_drawtext_vf` (`:68`), `resolve_drawtext_font` (`:30`), `run_label_videos` (`:81`) — burn `{index}` drawtext badge into compressed clips |
| `tasks/cover.py` | `extract_cover_frame` (`:33`) — pull poster frame at normalized timestamp from analysis; `_normalize_timestamp` (`:11`) |
| `tasks/waveform.py` | Peaks cache: `cache_key` (`:88`, sha1 of fingerprint), `peaks_from_pcm_s16le` (`:111`), `ensure_waveform` (`:397`), `extract_peaks_for_video` (`:312`), file-lock protocol `lock_status`/`write_lock`/`clear_lock` (`:156-198`), managed `_run_managed_waveform_task` (`:239`) as `TaskKind.WAVEFORM` |
| `tasks/reindex.py` | `auto_reindex_if_needed` (`:37`), `run_reindex` (`:92`) — rebuild `.vmeta`/`.vindex` from fresh probe data |
| `tasks/verify.py` | `run_verify` (`:11`) — check declared vs actual segments, missing/undeclared clips, stale sidecars |

## 3. Compression Flow (`tasks/compress.py` + `compress.py`)

```text
run_compress_all(config, files=None, single_file=None)
  ├─ enumerate sources (videos.json selection or input scan); skip offline
  ├─ for each source:
  │    auto_reindex_if_needed → existing_map of candidate outputs per key
  │    _find_reusable: reuse iff (.vmeta fresh) ∧ (settings unchanged) ∧ (ffprobe-readable)
  │      else compress_video: bitrate budget = target_size_mb - (audio budget if kept)
  │      → temp .part → verify duration → os.replace → prune stale siblings
  │    write .vmeta (VideoMeta.build) and the source .vindex segment table
  └─ summary / progress via tracker
```

Cache-correctness highlights:

- **Source key**: `_meta_source_key` uses the resolved `.vmeta.source_path` (`src:…`) before the
  bare basename key (`stem:…`), so same-basename videos never collide.
- **Freshness**: reuse only when `.vmeta.is_stale` is False **and** stored settings equal the
  current `_compress_settings_fingerprint` (`max_width`/`fps`/`target_size_mb`), **and** the
  output still probes readable. A source or settings change ⇒ re-encode.
- **Garbage collection**: `_prune_stale_siblings` deletes stale same-key outputs (file + `.vmeta`)
  once a fresh one is committed — preventing unbounded compressed-dir growth from OS iteration
  order (P1-04).
- `force` additionally drops every provably-same-source sibling, keeping exactly one output per
  source; legacy/no-sidecar or different-source files are never touched.

## 4. Cut (`cut.py` + `tasks/cut.py`)

- Time-source resolution: `cut_one` consumes absolute start/duration; segment offsets for legacy
  `_seg*` files are applied at the task level via `legacy_segment_offset_sec`.
- Default stream copy (`-c copy`) keeps cuts fast but non-frame-accurate; `--reencode` switches
  to x264 for precise edits.
- The batch runner maps plan segments (`use_timeline` ranges, `source` original/compressed) to
  output files, refuses to clobber fresh outputs (`CutBackupConflictError`), and reports missing/
  unreadable sources rather than silently passing.

## 5. Label & Cover

- **Label**: `run_label_videos` burns a `drawtext` overlay ({index} with left-top offset from
  `config.analyze`, font resolved per-platform via `resolve_drawtext_font`). Non-string/label
  sanitization via `safe_basename` on outputs. Cancel leaves partial mp4s removed.
- **Cover**: `extract_cover_frame` uses the analysis `cover_timestamp` to extract a poster frame
  into output `covers/`, so plan cards and export previews have a visual.

## 6. Waveform Peaks Cache (`tasks/waveform.py`)

**Cache protocol:**

```text
output/waveforms/<key>.json      ready payload {version, bin_count, peaks, source_fp}
output/waveforms/<key>.lock      generation lock {pid, started_at, source_path}
output/waveforms/<key>.error     last failure detail (cool-down 60s)
```

- `cache_key` = sha1 of `_file_fingerprint` (size + mtime + head bytes) — a file change
  invalidates the peaks; ffprobe errors are never cached as valid.
- `ensure_waveform` returns `ready`/`pending`/`error` without blocking; generation runs as a
  managed `TaskKind.WAVEFORM` task (`_run_managed_waveform_task` :239) bounded by a
  `BoundedSemaphore(MAX_CONCURRENT_JOBS=2)`.
- Lock protocol: `lock_status` treats dead-PID and same-process-orphan locks as `stale` (crash
  recovery); `clear_lock` on completion/failure.
- `peaks_from_pcm_s16le` decodes mono PCM with `struct.unpack_from` (no full-array allocation);
  bin count scales `bin_count_for_duration` between `[400, 2000]`.
- The UI stores a **composite** plan waveform on the client: per-source peaks are stitched on the
  plan axis in `plan-waveform.js` (see `10-ui-frontend.md`).

## 7. Reindex & Verify

- **Reindex** (`reindex.py`): probes current files, rebuilds `.vmeta` (source/target stats) and
  the `.vindex` segment table; `auto_reindex_if_needed` runs at pipeline/CLI/UI startup when
  sidecars are missing or frozen identity is stale. Trusts **only fresh split metadata** (no
  average-offset guessing — R-046 hardening).
- **Verify** (`verify.py`): reports missing segments, undeclared files, stale sidecars, and
  `video_offline` for originals in `videos.json`. Returns a non-zero exit code so CI/CLI callers
  can fail on drift.

## 8. Cross-Module Dependencies

- → `clio/utils` (subprocess wrappers, atomic writes, `safe_basename`, ffmpeg discovery)
- → `clio/vmeta` + `clio/identity` (sidecar read/write, legacy gate, offset resolution)
- → `clio/config` (`combine.compress`, `analyze`, `whisper`, `plan`, `naming`)
- → `clio/task_center` (waveform + cut run as managed tasks)
- → `clio/plan_model` + `clio/plan_readiness` (cut reads plan JSON; readiness pre-flight)

## 9. Risks / Maintenance Notes

1. `_find_reusable` and `_prune_stale_siblings` share the cache-key logic (`src:`/`stem:`) with
   `analyze.py`; the two implementations must stay in sync when the fingerprint/settings surface
   changes.
2. `compress_video` computes a target bitrate from `get_duration_sec`; a **codec/probe
   inconsistency** (ffprobe reading a different stream than the encoder writes) can produce a
   larger-than-target file when the estimate is wrong — the UI logs actual `ratio` for diagnosis.
3. Legacy `_seg*` handling keeps `legacy_segment_offset_sec` in the cut/verify/plan paths; new
   tasks must remember the gate or whole-file offsets silently apply to split files.
4. Waveform `.lock` files are **process-local authoritative**; the `stale` detection uses pid
   liveness and a 900s `STALE_SEC` — on a machine paused by sleep/resume a long encode can be
   spuriously declared stale and double-generated.
5. `run_verify`/`run_reindex` share artifacts with `ArtifactIndex`; a sidecar written by one but
   scanned by the other mid-write can transiently mismatch (atomic writes mitigate).