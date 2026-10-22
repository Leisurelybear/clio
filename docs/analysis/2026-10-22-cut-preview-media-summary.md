# Cut-Clip Preview Media Delivery Summary

Date: 2026-10-22

## Status

R-031b is implemented. Plan preview now prefers the rendered cut clip for a sequence entry and
falls back to the existing source-video seek path when a cut is missing, stale, or unreadable.
The composite clock, waveform, and plan domain stay unchanged.

## Resolution Order

1. A validated clip from the standard `output/cuts/<day>/` directory.
2. The R-031a source-video seek path using the compressed or original source.

Day concatenation is intentionally not used because there is no stable export artifact contract.

## Backend Contract

`GET /api/plan/preview-media?day=<day>` returns metadata only:

- `day` and `available`
- one entry per plan sequence position with `seg_index`, `clip_index`, relative `file`, planned
  duration, and source video index/timeline
- `file: null` when the cut is absent or rejected

The endpoint reads the saved plan and only the standard cuts root derived from
`paths.output_dir`. It does not accept a client filesystem path or a custom cut output directory.

`GET /api/video?source=cut&day=<day>&file=<basename>` serves a direct child of that cuts directory.
Both endpoints reject unsafe basenames, nested paths, symlinked roots/components, and paths outside
the project output.

## Clip Validation

- The expected name is generated with the same index/title sanitation as `run_cut_all`:
  `{video_index}_{title}_seg_{i:03d}.mp4`.
- Duplicate or non-direct files are never selected.
- A missing `_cut_info` sidecar is accepted for older/silent clips.
- A present `_cut_info` must match the clip index, timeline string, and numeric start/end fields;
  malformed or mismatched metadata is rejected.
- Partial cut runs are supported: mapped clips play, missing entries use source fallback.

## Frontend Behavior

- `plan-preview-media.js` is a pure resolver for cut-vs-source media, local seek time, player-time
  conversion, and preload identity.
- Cut clips seek from clip-local zero; source videos retain `planStart` and legacy `offset_sec`.
- The preloader cache key includes media source, project, file identity, and seek target, so two
  segments from the same source file do not share a stale preload.
- If a mapped cut fails to load, the viewer blocks that clip for the current mapping, retries the
  same composite time once from the source, and continues playback.
- Loading a plan or completing a cut refreshes the mapping; plan edits invalidate mismatched
  mappings through index/time compatibility checks.

## Verification

- Focused Python mapping/routes: `100 passed`; broader related pytest run: `278 passed, 1 skipped`.
- Full Vitest: `581 passed` across `58` files.
- New tests cover standard mapping, old clips without metadata, mismatched/broken metadata,
  duplicate sequence entries, partial cuts, symlink roots, traversal/nested media paths,
  clip-local timing, preload identity, and cut-to-source retry.

## Follow-Up

- Custom `cut-outdir` preview mapping remains unsupported by design.
- Day-level concatenated preview remains deferred until a versioned artifact contract and
  invalidation rules are defined.
