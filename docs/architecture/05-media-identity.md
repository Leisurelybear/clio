# Clio — Media Identity & Artifact Index

> Series: `05` of `00-overview.md`. Covers `clio/identity.py`, `clio/vmeta.py`, `clio/index.py`,
> plus the matching logic in `clio/ui/services/file_service.py` and `clio/ui/routes/videos.py`.

## 1. Responsibilities

- Persist a stable link between a **compressed clip** and its **original source** (and, for the
  legacy split format, segment offsets) in `.vmeta` per-file and `.vindex` per-source sidecars.
- Resolve the canonical media identity at runtime: `.vmeta` first, then `.vindex`, then filename
  parsing against `videos.json`/directory scans — so original/compressed/split matching survives
  renames, moves, and relinks.
- Provide one unified **artifact index** (`ArtifactIndex`) that maps compressed videos ↔ texts ↔
  script ↔ transcript ↔ cover for the UI and plan/export/verify flows.

## 2. Module Map

| File | Responsibility |
|---|---|
| `vmeta.py` | `VideoMeta` (`:28`) per-compressed-file sidecar with `source_path`/`target_path`/timestamps/`_quick_hash` verify + `is_stale`; `SplitInfo` (`:19`); `VideoIndex` (`:134`) per-source segment table with `compressed_paths`/`declared_paths`/`is_stale`; `_quick_hash` (`:229`) sampled hash; atomic JSON writes |
| `identity.py` | `MediaIdentity` dataclass (`:83`); `resolve_identity` (`:148`, sidecar→vindex→filename); `_extract_original_stem` (`:81`); `is_legacy_split_*`/`legacy_segment_offset_sec` gate (`:39`/`:45`); `load_identity` (`:331`) reads `media_identity` from JSON artifacts; `prefer_canonical_compressed` (`:302`); `SEGMENT_SUFFIX_RE` (`:13`, `_seg/_part/_pt/_chunk` aliases) |
| `index.py` | `ArtifactIndex` (`:97`) — scans `compressed/texts/scripts/transcripts/covers` once, builds `_by_compressed_stem`/`_by_original_stem` maps; `VideoEntry`/`TextEntry`/`ScriptEntry`/`TranscriptEntry`/`CoverEntry`/`ArtifactGroup` (`:23-66`); reports ambiguous legacy artifacts (`ambiguous_artifacts`) |
| `ui/services/file_service.py` | `_find_original_for_compressed` (`:249`) and `_find_compressed_for_original` (`:351`) — `.vmeta`/`.vindex` first, `videos.json` stem match, then disk scan; `_matches_selected_*` identity-only JSON matchers |
| `ui/routes/videos.py` | Relink, selection, video listing using the above; `handle_put_videos_relink` (`:787`) |

## 3. Sidecar Schema (`vmeta.py`)

**`.vmeta`** (one per compressed file, written next to it, `VMETA_EXT = ".vmeta"`):

```json
{
  "version": 1,
  "data": { "source_path": "…", "target_path": "001_GL010683.mp4",
            "is_original": false, "is_split_segment": false, "split_info": null },
  "source_modifyTime": 0, "source_size": 0, "target_modifyTime": 0, "target_size": 0,
  "source_duration_sec": 0, "target_duration_sec": 0,
  "compress_settings": { "fps": 15, "codec": "libx264", "crf": 32 },
  "verify": "<sha256 sample hash>"
}
```

`is_stale` (`.vmeta:108`) re-checks source+target mtime/size plus the sampled `verify` hash, so a
modified source forces recompression. `_quick_hash` reads head + mid + tail chunks (not the whole
file) for cheap integrity.

**.vindex** (one per *source*, `{source_stem}.vindex` in the compressed dir) — segment table for
the legacy split format:

```json
{ "version": 1, "source_stem": "GL010683", "source_path": "…", "source_size": 0,
  "source_modifyTime": 0, "source_duration_sec": 0, "is_split": true,
  "segments": [ { "index": "1", "filename": "001_GL010683_seg01.mp4",
                  "offset_sec": 0, "duration_sec": 30, "segment_number": 1,
                  "total_segments": 5 } ] }
```

`declared_paths` (`.vmeta:203`) returns **all** declared segments (even missing ones) so `verify`
can report gaps instead of silently passing; `compressed_paths` filters to existing files.

## 4. Identity Resolution (`identity.py:resolve_identity`)

Priority order — never trust the filename over a sidecar:

1. **`.vmeta` sidecar**: authoritative `original_stem`/`source_path` + `split_info` offsets.
2. **`.vindex`** (exact indexed stem → legacy suffix-stripped stem): usable only if it actually
   *lists the clip* (`_vindex_contains`, `.vmeta:41`) — a stale vindex can never re-associate a
   clip with the wrong source.
3. **Filename parsing vs `videos.json`** (via `_find_original_by_stem`): `001_GL010683` →
   `GL010683`, handling `_seg/_part/_pt/_chunk` aliases; legacy collocated layout fallback when
   no `videos.json` selection exists.

The same ordering is mirrored in `file_service._find_original_for_compressed` /
`_find_compressed_for_original` for the video list UI, relink, and export material resolution.

## 5. Legacy Split Gate (read-only)

Physical splits were removed (R-037/038) — `_segNN` files are now **legacy read-only** inputs:

- `is_legacy_split_*` (`identity.py:39`/`:47`) and `legacy_segment_offset_sec` (`:45`) gate every
  downstream consumer (cut/export/plan/transcript_align); only these files ever apply segment time
  offsets, so whole-file compressed clips are never mis-offset.
- `prefer_canonical_compressed` (`identity.py:302`) lets callers drop legacy split clips when a
  canonical whole-file compression exists.

## 6. Artifact Index (`index.py`)

`ArtifactIndex` is built once per project and queried many times (UI + plan/verify):
`build()` scans compressed/texts/scripts/transcripts/covers in order, then groups by compressed
stem. Index keys are **normalized** via `expand_index_keys("1") ≡ expand_index_keys("001")`
(`clio/plan_readiness.py`) so int-typed indices in legacy texts never orphan a group
(`_stems_for_index`, `index.py:88`). Ambiguous same-index legacy artifacts are collected in
`ambiguous_artifacts()` instead of silently attaching to the first match.

`load_identity(data)` (`identity.py:331`) reads the `media_identity` block embedded in v2 JSON
artifacts (analysis/transcript), returning `None` for v1 files — the batch writers embed identity
so the index and export layer never rescan for the original path.

## 7. Cross-Module Dependencies

- → `clio/utils` (`write_json_atomic`)
- → `clio/tasks._video_loader` (`load_selected_videos` — `videos.json` manifest)
- → `clio/plan_readiness` (`expand_index_keys`)
- Consumed by: compress (writes `.vmeta`), reindex (rebuilds), verify, texts/scripts/plan
  embedding, UI video list + relink, export material resolution.

## 8. Risks / Maintenance Notes

1. Three parallel resolution trees exist (`resolve_identity`, `file_service._find_*`,
  `ArtifactIndex._scan_*`) — they agree today but each re-implements stem/vindex/preference
  logic; a contract change (e.g. new sidecar format) must update all three.
2. `.vmeta.read` returns `None` on *any* parse error (`.vmeta:102`) — a corrupt sidecar silently
  degrades to filename resolution. consider surfacing corruption during `verify`.
3. `resolve_identity` accepts both `(compressed, index_str, project_dir)` and the legacy
  `(compressed, input_dir_Path, index_str)` signature — the overload is undocumented in type
  hints and easy to misuse (P2-P13/P1-02 history).
4. `ArtifactGroup.compressed` starts as an **empty** `VideoEntry` (`index.py:180`) — a text/script
  orphan (no compressed file) will carry a path of `Path()`. Consumers must handle empty paths; a
  missing-compressed group is still returned to reporters (verify relies on this).