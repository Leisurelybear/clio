# Design: Plan-preview floating subtitles

**Date**: 2026-08-05  
**Status**: Implemented ✅ — landed via `2026-08-05-plan-subtitles.md` (core overlay) + `2026-08-07-plan-subtitle-settings-plan.md` (customization); see `clio/ui/static/src/plan-subtitle.js` and `preview.subtitles` config (P2-P33 sync)  
**Scope**: Plan entity preview — overlay spoken `voiceover` text as floating subtitles on the main player  
**Approach**: A — pure-frontend on-demand load (new `plan-subtitle.js`, no backend/plan-file changes)  
**Related**: R-031 plan composite preview (`viewer.js` preview bar), `/api/voiceover` route, plan `use_timeline` per segment

## 1. Goals and non-goals

### Goals

1. In **plan mode** (`currentEntity === 'plan'`), while previewing the composite timeline, show the current segment's spoken narration text as **floating subtitles** overlaid on the main `<video id="player">`.
2. Subtitle text reuses the **existing `scripts/*_voiceover.json`** `voiceover` field (no new plan/prompt fields, no backend changes).
3. Lines **rotate over the segment's `use_timeline` duration** at even intervals (匀速分配): for a segment with duration D and N lines, line i spans `[i·D/N, (i+1)·D/N)`.
4. Keep all slicing / scheduling / lookup logic as **pure functions** unit-testable with Vitest.
5. Subtitles follow the existing preview playhead (`state.previewGlobalSec`) — single source of truth, no separate timing drift.

### Non-goals

- Subtitles for ordinary (non-plan) video preview — deferred, out of scope this change.
- Overlaying subtitles into the exported video files / cut output — preview-only.
- Aligning subtitle timing to `transcript` ASR timestamps — plan-choice is even distribution over `use_timeline`.
- Editing subtitle text in the UI.
- Backend route, plan-file schema, or prompt changes.

### Success criteria

- In plan mode preview, subtitles appear over the player only for segments that resolve to a video with an existing `script_json` and non-empty `voiceover`.
- Subtitle line advances in real time as the playhead crosses line boundaries within the segment.
- No subtitles (layer hidden) when: leaving plan mode, segment duration is 0 / no `use_timeline`, missing `script_json`, empty `voiceover`, fetch failure, or segment local time is outside the range.
- Pure helpers have Vitest coverage; existing frontend tests still pass.

## 2. Data flow

```
plan segment (index="001", use_timeline="00:10-00:45")
   │  index
   ▼
state.videos.find(v => String(v.index) === seg.index)
   │  v.script_json  (e.g. "001_..._voiceover.json")
   ▼
GET /api/voiceover?file=<script_json>   →  { voiceover, ... }
   │
   ▼
splitSubtitleLines(text) → [s0, s1, … sN-1]
   │
   ▼
scheduleSubtitleTiming(seg.duration, N) → [{startSec, endSec, index}]
   │
   ▼
subtitleIndexAtTime(localSec) → current line index (or null)
   │
   ▼
#plan-subtitle overlay textContent = lines[index]
```

- `segment.duration` = `parseRange(use_timeline).duration` (already provided by `plan-timeline.js` `buildTimeline` → `segments[i].duration`).
- Current `localSec` = `state.previewGlobalSec − segment.globalStart` (both plan-domain, from `viewer.js`). This matches the existing `globalToLocal` semantics (viewer.js:195-211), so subtitle timing stays aligned with the composite playhead.
- Guard against stale in-flight fetches: at resolve time verify the segment index still equals the currently-previewed segment before applying text (see §6).

## 3. New module: `clio/ui/static/src/plan-subtitle.js`

Pure functions (no DOM in these three):

- **`splitSubtitleLines(text: string, maxLen = 16): string[]`**
  - Split on Chinese punctuation (。！？；…和换行), then further break lines longer than `maxLen`.
  - Empty / whitespace-only input → `[]`.
- **`scheduleSubtitleTiming(durationSec: number, lineCount: number): Array<{startSec: number, endSec: number, index: number}>`**
  - If `lineCount <= 0` or `!Number.isFinite(durationSec)` or `durationSec <= 0` → `[]`.
  - Line `i` spans `[i·d/N, (i+1)·d/N)`, last line clamped to `durationSec`.
- **`subtitleIndexAtTime(schedule, localSec): number | null`**
  - Returns matching line index for `localSec`, or `null` when out of range / empty schedule.

DOM/loader helpers (non-pure, exported):

- `loadVoiceoverText(index): Promise<string | null>` — caches `Map<index, text>`; fetches `/api/voiceover` via `api()`; on failure returns `null` (log to console, do not disturb user status).
- `updatePlanSubtitle(lines, schedule, localSec, el)` — sets `el.textContent`; skips DOM write when line unchanged; toggles `hidden`.

## 4. UI overlay

- **HTML** (`clio/ui/static/index.html`), inside `.player-wrap` after `<video id="player">`:

```html
<div id="plan-subtitle" class="plan-subtitle" hidden></div>
```

- **CSS** (adjacent to `.player-wrap` rules): absolutely positioned over the player, centered, bottom offset (~24px), semi-transparent dark pill background, white text, larger font, text shadow/描边 for readability on any frame. `z-index` above the video; pointer-events none.

```css
.plan-subtitle {
  position: absolute; left: 50%; transform: translateX(-50%);
  bottom: 24px; max-width: 80%; text-align: center;
  padding: 6px 14px; border-radius: 8px;
  background: rgba(0,0,0,.55); color: #fff;
  font-size: 22px; line-height: 1.4; pointer-events: none; z-index: 4;
  text-shadow: 0 1px 3px rgba(0,0,0,.8);
}
```

- `.player-wrap` currently has `display:flex; justify-content:center; aspect-ratio:16/9; overflow:hidden` and **no** `position` (style.css:765). Add `position: relative` so the absolute overlay anchors to it.

## 5. Wiring into `viewer.js`

- **Segment change** (`seekToGlobal`, after `_loadAndSeekSource`): resolve the current segment's index → `state.videos` → `script_json`. If payload is currently cached, update immediately; else preload via `loadVoiceoverText` then update.
- **timeupdate** (existing `player.ontimeupdate`): after `state.previewGlobalSec` is computed, call subtitle update with `localSec = previewGlobalSec − seg.globalStart`.
- **startPreview / stopPreview**: start → recompute current segment; stop → hide layer.
- **Leave plan mode** (`renderPreviewBar` non-plan branch and `_autoSwitchSegment`): hide layer.
- Guard: only run when `currentEntity === 'plan' && previewIndex >= 0`.

## 6. Error handling & edge cases

- Missing `script_json` / empty `voiceover` / zero duration / fetch 404 or network error: hide layer silently, never block playback, no `setStatus` noise.
- Segment with no `use_timeline` (duration 0): `<video>` still plays the selected clip; subtitles hidden.
- Rapid segment switches: stale in-flight fetches must not overwrite a newer segment's subtitle (guard by comparing expected index at resolve time).

## 7. Testing

- **Vitest** (frontend):
  - `splitSubtitleLines`: Chinese punctuation splitting, long-line wrap, empty input → `[]`.
  - `scheduleSubtitleTiming`: even distribution, lineCount=0 / non-finite / zero duration → `[]`, last-line clamp.
  - `subtitleIndexAtTime`: boundary (`startSec` inclusive, `endSec` exclusive), out-of-range → `null`, empty schedule → `null`.
- No Python tests — pure frontend change (per repo convention).
- Verify: `npm test`, and `python -m pytest clio/tests/` unchanged (no backend edits).

## 8. Open questions

- None blocking. (Optional UX tweaks, e.g. subtitle line count / maxLen, can be tuned during implementation.)