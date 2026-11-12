# Design: GoPro GPMF telemetry as highlight signal (R-024)

**Date**: 2026-07-16  
**Status**: MVP + R-024b opt-in analyze injection (`analyze.use_gpmf`, default false) + Q4 default planning metadata (`7189ede`).

## Problem

Visual-only AI analysis misses motion peaks (speed spikes, elevation change, rapid turns) that often mark the best sport/travel highlights. GoPro embeds GPMF telemetry in MP4; we can surface it as structured context for `video_analyze` / plan.

## Hard constraint: GPS is optional

**Not every video has GPS or GPMF.** Phone footage, indoor clips, and many GoPro modes (no GPS lock, telemetry off, multi-clip without sensors) must work unchanged.

| Case | Expected behavior |
| --- | --- |
| No GPMF in file | `has_gpmf=False`, empty prompt block, pipeline continues |
| GPMF marker but no GPS samples | probe-only note optional; never error |
| Sidecar present | use peaks/elev and, for planning, capture time/GPS when available |
| `video_path` missing / unreadable | empty summary, no exception |

## Goals (MVP)

1. Given an original MP4 path, extract a **compact telemetry summary** when GPMF-like samples exist.
2. Format that summary as a short, prompt-safe text block for AI context.
3. **Never require GPS/GPMF** to run the pipeline — missing telemetry is a silent no-op.

## Non-goals (MVP)

- Full GPMF binary parser for every camera firmware
- GPS track export / map UI
- Replacing visual analysis
- Blocking analyze when telemetry is absent
- Assuming every project or every clip has GoPro GPS

## Approach

### Why not a full GPMF dependency first?

A complete GPMF demux is large and format-fragile. MVP uses a **layered strategy**:

| Layer | Source | Use |
| --- | --- | --- |
| A (MVP) | Optional sidecar JSON `*.gpmf.json` next to original, or inline test fixture | Deterministic tests + user-provided extracts |
| B (MVP) | Scan MP4 for ASCII `GPMF` marker + payload size heuristic | Cheap “has telemetry” flag |
| C (later) | `gpmf-parser` / ffmpeg data stream / external CLI | Real GPS/accel time series |

Layer A unblocks product experiments without shipping a fragile binary parser.

### Data model

```python
@dataclass
class TelemetrySummary:
    has_gpmf: bool
    source: str  # "sidecar" | "probe" | "none"
    duration_sec: float | None
    sample_count: int
    # optional series (normalized 0..1 or physical units when known)
    speed_peaks: list[dict]   # [{t_sec, value, unit}]
    elev_delta_m: float | None
    capture_time: str | None  # normalized ISO-8601 capture timestamp
    latitude: float | None
    longitude: float | None
    notes: list[str]
```

### Prompt injection (later wiring)

When `has_gpmf` and peaks exist, append to analyze context:

```text
### 运动遥测摘要（GoPro GPMF）
- 速度峰值约在 00:12, 01:03（相对片段起点）
- 海拔变化约 +120 m
请优先考虑这些时刻附近的动作/风景作为 highlight。
```

MVP delivers `format_telemetry_for_prompt(summary) -> str` only; analyze.py wiring is a follow-up commit if peaks prove useful.

## Planning metadata (Q4)

Planning always attempts to read the sidecar next to the original clip, independently of `analyze.use_gpmf`. Missing, corrupt, invalid, or non-GoPro sidecars remain a silent no-op. When valid, capture time is preferred over ffprobe and filename inference; a latitude/longitude pair is included only when both coordinates are finite and in range. Sidecar-only metadata remains available when the original video is temporarily offline.

## File layout

- `clio/gpmf.py` — pure functions: load sidecar, probe marker, summarize, format
- `clio/tests/test_gpmf.py` — TDD coverage
- Config flag: `analyze.use_gpmf` default false until validated; when true, `merge_telemetry_into_context` appends the block to analyze `context_override`

## Acceptance (MVP)

- [x] Unit tests for sidecar parse + format + missing file
- [x] `probe_gpmf_marker(path)` returns bool without crashing on random files
- [x] No pipeline dependency; import is lazy-safe
- [x] Optional inject into analyze when original path known (`analyze.use_gpmf`, `merge_telemetry_into_context`)

## Risks

- Sidecar workflow is manual until Layer C
- Timestamp alignment with split segments needs `offset_sec` when injecting
