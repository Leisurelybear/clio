# Design: Plan-preview subtitle customization

**Date**: 2026-08-07
**Status**: Draft — awaiting implementation plan
**Scope**: Extend the plan-preview floating subtitles into a customizable, editable, position-draggable layer
**Approach**: A — project-scoped config node + frontend renderer enhancement (no new dedicated backend routes)
**Related**: `docs/superpowers/specs/2026-08-05-plan-subtitles-design.md` (base overlay), `clio/ui/static/src/plan-subtitle.js`, `clio/ui/static/src/editor-plan.js`, `/api/voiceover` (GET/PUT), `/api/config/project` (GET/PUT), `clio/config/*`

## 1. Goals and non-goals

### Goals

1. **Style** (`①`): Let the user adjust subtitle **font size, font family, text color, background, outline** from a settings panel in the plan page. Styling is no longer hard-coded in `style.css`; it is driven by config values injected as CSS custom properties onto `.plan-subtitle`.
2. **Position + drag** (`②`): Show a persistent drag handle on the subtitle layer; dragging moves the subtitle within the player and persists its position (percentage-based, resizes correctly with the window).
3. **Edit + display in plan** (`③`): Add a subtitle edit block in each expanded plan segment that loads the segment video's `voiceover` text, allows in-place editing, and saves back to the same `scripts/*_voiceover.json` the preview reads from — so edits are immediately reflected in the preview (所见即所得). The resolved current subtitle is also visible there.
4. **Long-line optimization** (`④`): Mutually-exclusive **mode selector** (`auto | multi | scroll`), configurable `max_len_per_line`, configurable **auto-shrink font** (`min_font_size`), and optional scroll behavior, so long paragraphs no longer hard-split into awkward fixed time-slices.

### Non-goals

- Burning subtitles into exported video / cut output — remains preview-only.
- Timing subtitles to `transcript` ASR timestamps — still even distribution over `use_timeline`.
- A dedicated new backend route — reuses existing generic config + voiceover routes.
- Editing the source `plan` `voiceover_hint` as the rendering source — rendering continues to read `voiceover` field of the voiceover JSON (single source of truth).

### Success criteria

- Style settings rendered onto the overlay from config; toggling any style control updates the overlay instantly.
- Subtitle draggable via a persistent handle; release persists to `project.yaml`; position survives reload and stays correct across player resizes (percent-based).
- In plan segment expand, an edit block shows the current voiceover text; saving writes `/api/voiceover` and the preview immediately shows the new text (cache invalidated).
- Mode selector `auto/multi/scroll` changes rendering; long sentences pack into `max_lines` simultaneous lines or scroll rather than hard-slicing into many time slices.
- Vitest coverage for new pure helpers; existing frontend and backend tests still pass.

## 2. Configuration schema

Add a new **project-level** section `preview` (dataclass `PreviewConfig`) containing a nested `PreviewSubtitlesConfig`.

```yaml
preview:
  subtitles:
    enabled: true          # master toggle for plan subtitles
    mode: auto             # auto | multi | scroll (mutually exclusive)
    max_lines: 2           # simultaneous lines in auto/multi
    max_len_per_line: 16   # target max characters per line
    min_font_size: 14      # floor for auto font shrink (px)
    scroll_speed: 40       # horizontal scroll speed px/s (scroll mode)
    font_size: 22          # base font size px
    font_family: ""        # empty = follow system
    font_color: "#ffffff"
    background: "rgba(0,0,0,0.55)"
    outline: "1px solid #000"
    pos_x: 50              # horizontal center (percent of player width)
    pos_y: 8               # vertical offset from bottom (percent of player height); 0=bottom, 100=top
```

Placement note: `PreviewConfig` is added to the project-only section map so it persists under the selected project and is shared/backed up with it.

- `models.py`: add `@dataclass class PreviewSubtitlesConfig` and `@dataclass class PreviewConfig` (`subtitles: PreviewSubtitlesConfig = field(default_factory=...)`); register `preview` in `AppConfig`.
- `loader.py`: add `"preview": PreviewConfig` to `_PROJECT_SECTION_DC_MAP` (and to `_PROJECT_ONLY_SECTIONS`), implement the project-load branch.
- `validators.py`: validate `mode` in `{auto, multi, scroll}`, `pos_x/pos_y` within 0..100, `max_lines >= 1`, `max_len_per_line >= 1`, `min_font_size <= font_size` (clamp rather than error is also acceptable).
- `descriptions.py`: Chinese descriptions for each key.
- Update `docs/project.example.yaml`, `config.example.yaml` (if needed), README(s).

A merged property `AppConfig.preview` is added with project fallback (pattern matched by `plan`/`export`).

### Config-loading assumptions (verified)

- `loader.py` builds project config from the explicit `_PROJECT_SECTION_DC_MAP` + `_PROJECT_ONLY_SECTIONS` allowlists, so `preview` must be added to **both** (models.py:51-60, loader.py:245-280).
- `_upgrade_config_file` walks `_PROJECT_SECTION_DC_MAP` and auto-adds missing keys' defaults (loader.py:145-175) — adding `preview` there gives free auto-upgrade of new subtitle defaults on existing projects.
- The config editor UI (`editor-config.js` `_renderConfigProject`, line 345) renders from an explicit `order` array; `preview` is **not** added there, so it does **not** surface in the settings page — subtitle styling is exposed only in the plan page (avoids duplicate UI). Intended.

## 3. Renderer changes (`plan-subtitle.js`)

### 3.1 Style binding

- `applySubtitleStyle(style)` — pure-ish DOM helper that sets CSS custom properties on `#plan-subtitle` from config:
  - `--st-font-size`, `--st-font-family`, `--st-color`, `--st-bg`, `--st-outline`, `--st-pos-x`, `--st-pos-y`.
- `style.css` replace hard-coded values with `var()` references.
- `renderPlanSubtitle` reads `preview.subtitles` from `state.configProject` (merged with defaults when absent) and applies style before/with text update.

### 3.2 Line layout / scheduling (pure functions reworked)

- Keep signature-friendly pure helpers:
  - `splitSubtitleLines(text, opts)` where `opts = { maxLen, maxLines, mode }`.
    - Sentence-first splitting (existing punctuation), then pack into **batches** of up to `maxLines` lines in `auto`/`multi`.
    - In `auto`: short sentences → single line; long accumulated text → packs into `maxLines` lines.
  - `scheduleBatchTiming(durationSec, batchCount)` — even time slices per batch (replaces per-line scheduling).
  - `subtitleIndexAtTime(schedule, localSec)` — unchanged semantics, indexes batches now.
- `packSentenceBatches(sentences, { maxLines, maxLen })` — new pure helper that groups sentences into display batches, preferring to keep a sentence intact and fill remaining width.

### 4.3 Auto font shrink

- `computeFontShrink(text, basePx, { containerMaxChars, minFontSize })` — pure heuristic returning effective font size when text would overflow container; only in `auto`/`multi` with `min_font_size` floor.
- Applied in the DOM via `--st-font-size` when needed; restores base when not.

### 4.4 Scroll mode

- When `mode === 'scroll'`, render as a single line and use `--st-scroll-speed` with `scroll` animation (CSS `overflow: hidden` + inner track). Pause on hover optional; non-blocking.

### 4.5 Cache invalidation

- Export `invalidateVoiceoverCache(index?)` so the plan edit save clears it, forcing re-fetch on next preview.

## 5. Position drag (persistent handle)

- HTML: inside `.plan-subtitle`, add a child handle:
  ```html
  <div id="plan-subtitle" class="plan-subtitle" hidden>
    <span class="plan-subtitle-handle" title="拖动字幕位置">⠿</span>
    <span class="plan-subtitle-text"></span>
  </div>
  ```
  (Renderer currently uses `el.textContent`; refactor to set `.plan-subtitle-text` inner text and put handle as sibling/child.)
- **CSS**: `.plan-subtitle` gets `pointer-events: none` (unchanged, so video + drags aren't blocked); `.plan-subtitle-handle` gets `pointer-events: auto; cursor: grab`. Handle visible only in plan mode while subtitles are shown; subtle, `opacity` + hover highlight.
- **JS** (new module or in `plan-subtitle.js`): `enableSubtitleDrag()` binds `pointerdown/move/up` on the handle:
  - On move, update `state.previewSubtitlePos = { x: %, y: % }` live.
  - On up, write `preview.subtitles.pos_x/pos_y` into `state.configProject`, then `PUT /api/config/project` (debounced/single).
  - Position stored as top-left-relative percent of player; clamp 0..100.
  - On window/player resize, recompute using percentages (no drift).

## 6. Plan editor UI (`editor-plan.js`)

In each expanded segment's `.plan-seg-panel`, after the existing 口播 textarea, add a 字幕 block:

```html
<div class="plan-seg-subtitles">
  <label>字幕 <textarea rows="3" data-k="subtitle_edit"></textarea></label>
  <button data-subtitle-save>保存字幕</button>
  <span class="plan-seg-subtitle-status"></span>
</div>
```

- Load: when segment expands, resolve `seg.index → videos[].script_json → GET /api/voiceover`; fill `textarea` with the `voiceover` string (or show "无口播文案" hint if missing). Keep the **whole** loaded JSON object (`title`/`edit_tip`/`duration_hint_sec` etc.), not just the string, so a later merge preserves them.
- Edit → store draft in memory for that segment + `markDirty()`.
- Save → `PUT /api/voiceover?file=<script_json>` with **`{ ...loadedVoiceover, voiceover: <new text> }`** — spread the original object so other voiceover fields are preserved (editor-voiceover.js:59-58 prove those fields exist). On success `invalidateVoiceoverCache(index)` and update the preview; show status (`已保存` / error).
- Readiness/tab-switch dirty guard: `shouldConfirmDirtyTabSwitch` treats subtitle draft as dirty (extend existing `editor-save.js` handling).
- **Ctrl+S save-target consistency (review finding)** — with `entity === 'plan'`, `resolveEditorSaveTarget` returns `{ action: 'plan' }` → `PUT /api/plan` (editor-save.js:20-21), which would `clearDirty()` and **silently drop any unsaved subtitle draft** that shares the same dirty flag. Decision:
  - Subtitle editing is **self-contained** under its own 保存字幕 button; that is the canonical save path for subtitle text.
  - Track subtitle drafts under a **separate signal** (`state.subtitleDirtyIndexes: Set<index>`), not the shared plan dirty flag alone.
  - When the plan save (Ctrl+S / 保存) runs while `subtitleDirtyIndexes` is non-empty, either (a) refuse+提示 "有未保存的字幕，请先点击对应片段的 保存字幕", or (b) flush the drafts via `/api/voiceover` first. Pick the least-surprise option during implementation; spec keeps the requirement (no silent drop).

## 7. Viewer wiring (`viewer.js`)

- Call the renderer through existing `seekToGlobal` + `ontimeupdate` paths (no structural change).
- Apply subtitle style on plan enter (`renderPreviewBar` plan branch) and after config load; hide layer on leave/stop as today.

## 8. Error handling & edge cases

- Missing `script_json` / empty `voiceover`: plan edit block shows an empty-state hint, no blocking.
- Voiceover save failure: show error status, keep dirty so user can retry; do not corrupt file (atomic save existing).
- Config absent or partially filled: apply defaults (`enabled=true`, `mode=auto`, etc.).
- Drag when player resized mid-drag: clamp with latest rect.
- Rapid segment switches during async load: existing stale-fetch guard stays; ensure per-index.

## 9. Testing plan

- **Vitest** (frontend): `splitSubtitleLines` batches, `packSentenceBatches`, `scheduleBatchTiming`, `computeFontSize`, `(subtitle new helpers)` plus updated `renderPlanSubtitle/hidePlanSubtitle` DOM tests and `invalidateVoiceoverCache`. Existing `plan-subtitle.test.js`, `editor-save.test.js` updated/kept green.
- **Pytest** (backend): config section dataclass/loader/validator tests for `preview`; examples docs smoke.
- Verify: `npm test`, `python -m pytest clio/tests/`, `ruff check`, mypy gate.

## 10. Open questions

- None blocking. `pos_y` is fixed as **bottom-offset percentage** (0 = player bottom, 100 = top) for parity with the current `bottom: 24px`; the drag + clamp logic in §5 confirms to this basis.