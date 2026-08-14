# Design: Subtitle background / outline friendly editors

**Date**: 2026-08-14
**Status**: Draft — awaiting implementation plan
**Scope**: Replace the raw-CSS text inputs for subtitle `background` and `outline` in the plan-preview subtitle settings panel with color-picker + slider controls
**Approach**: A — frontend-only refactor; keep the existing CSS-string config format for full backward compatibility
**Related**: `docs/superpowers/specs/2026-08-05-plan-subtitles-design.md`, `docs/superpowers/specs/2026-08-07-plan-subtitle-settings-design.md`, `clio/ui/static/src/subtitle-settings.js`, `clio/ui/static/src/plan-subtitle.js`, `clio/ui/static/style.css`, `clio/config/models.py` (`PreviewSubtitlesConfig`)

## 1. Goals and non-goals

### Goals

1. Let the user set subtitle **background** and **outline** without hand-typing CSS syntax.
2. `background` → **color picker + opacity slider (0–100%)**; the picker controls color, the slider controls alpha.
3. `outline` → **color picker + width slider (0–10px, step 1)**; the picker controls color, the slider controls the `text-shadow` blur radius (thickness).
4. A small readout under each group shows the compiled `rgba(...)` / `text-shadow` string so users can see what is persisted.
5. Live preview: picking a color or moving a slider updates the floating subtitle instantly (existing `input` → `onChange` → `renderPlanSubtitleFromState` chain, viewer.js:394-401).
6. Full backward compatibility: existing `project.yaml` values like `background: "rgba(0,0,0,0.55)"` and `outline: "0 0 2px rgba(0,0,0,0.8)"` keep working and pre-fill the new controls.

### Non-goals

- Changing the config schema or the persisted string format (`background` stays CSS, `outline` stays `text-shadow`).
- Any backend / model / validator / description changes.
- Adding gradient backgrounds, multiple outlines, or drop-shadow blur in addition to outline width.
- Burning subtitles into exported video — remains preview-only (unchanged).
- Touching `font_color` (already a native color picker) or `font_family`.

### Success criteria

- The panel renders color pickers + sliders instead of raw text inputs for `background`/`outline`.
- Round-trip: parsing an existing config string and re-serializing from untouched controls produces an equivalent value (no data loss on unrelated edits).
- Legacy hex / `rgb` / `rgba` (with or without spaces) backgrounds and `0 0 <n>px rgba(...)` outlines parse correctly.
- Unparseable legacy values fall back to defaults in the controls but are **not** silently rewritten unless the user edits that row.
- Vitest unit coverage for new parse/serialize helpers and panel render/emit; existing frontend + backend tests still pass.

## 2. Data flow (unchanged persistence)

Values stay stored as before and are consumed by the same renderer:

- `background` → CSS `background` of `.plan-subtitle` via `--st-bg` (style.css:875, plan-subtitle.js:326).
- `outline` → CSS `text-shadow` of `.plan-subtitle` via `--st-outline` (style.css:878, plan-subtitle.js:327-330).

The only change is the panel UI (subtitle-settings.js) and its emit path: it now composes `background`/`outline` from the picker+slider instead of reading text input. `mergeSubtitleSettings` still writes the composed CSS strings through `PUT /api/config/project` (viewer.js:391-392). No schema, model, or renderer-internals change.

## 3. New pure helpers (`subtitle-settings.js`)

Keep them exported and dependency-free so Vitest can cover them:

- `parseRgba(str): {r,g,b,a} | null`
  - Parse `rgba(r,g,b,a)`, `rgb(r,g,b)`, hex `#rgb`, `#rrggbb`, `#rrggbbaa` (case-insensitive; tolerate optional spaces).
  - Return channel channels 0–255 and alpha 0–1; else `null`.
- `serializeRgba(color, alpha): string`
  - Compose `rgba(r,g,b,a)` with alpha rounded to 2 decimals (e.g. `rgba(0,0,0,0.55)`).
- `parseTextShadow(str): {widthPx, color} | null`
  - Parse `[0] [0] <n>px rgba(...)` → width (px, clamp ≥ 0) + parsed color.
- `serializeTextShadow(widthPx, color): string`
  - Compose `0 0 <n>px rgba(...)`.

Width slider constant `OUTLINE_MAX_PX = 10` (single source of truth in subtitle-settings.js; width 0 → no visible outline, width value still serialized).

## 4. Panel rework (`subtitle-settings.js` `renderSubtitleSettingsPanel` / `emit`)

### 4.1 Field model

`subtitleControlsModel()` additionally derives:
- `bg_color` (hex for picker) + `bg_opacity` (0–100 int) from parsing `s.background`;
- `outline_color` (hex) + `outline_width` (px int) from parsing `s.outline`.

### 4.2 Template

Replace the two text inputs (subtitle-settings.js:146-147) with:

```html
<label>背景色
  <div class="st-color-row">
    <input type="color" data-subtle="bg_color" value="#000000">
    <input type="range" min="0" max="100" step="1" data-subtle="bg_opacity" value="55">
    <span class="st-color-value" data-subtle-badge="background">rgba(0,0,0,0.55)</span>
  </div>
</label>
<label>描边
  <div class="st-color-row">
    <input type="color" data-subtle="outline_color" value="#000000">
    <input type="range" min="0" max="10" step="1" data-subtle="outline_width" value="2">
    <span class="st-color-value" data-subtle-badge="outline">0 0 2px rgba(0,0,0,0.8)</span>
  </div>
</label>
```

### 4.3 emit

- For the `background` group: read `bg_color` + `bg_opacity`, compose via `serializeRgba`, write `background`.
- For the `outline` group: read `outline_color` + `outline_width`, compose via `serializeTextShadow`, write `outline`.
- Both remain in `STRING_KEYS` handling but are now output from composed values; `safeCssShadow` fallback still guards serialization.
- Keep `change` + `input` listeners on `[data-subtle]` so sliders/pickers give live preview, and refresh the `data-subtle-badge` text on each emit.

### 4.4 Legacy / unparseable safeguard

On render, track the *original raw string* per group (in a data attribute). If a stored value fails `parse*`, the controls show defaults but `emit` only writes that group back when the user has interacted with it (interaction toggled via a `touched` flag on the row). This prevents an unrelated field change from silently rewriting a previously fine (but unparseable) custom value.

## 5. Styling (optional, `style.css`)

Add a minimal `.st-color-row` layout (flex: picker + slider + badge) and `.st-color-value` mono badge styling inside the existing `.subtitle-settings-grid`. No global changes.

## 6. Testing

- `cli/ui/static/src/__tests__/subtitle-settings.test.js`:
  - `parseRgba` round-trips for `rgba(0,0,0,0.55)` (spaced + unspaced), `rgb(r,g,b)`, `#fff`, `#ffffff`, `#ffffffcc`; returns `null` for invalid.
  - `serializeRgba` alpha rounding (0.55, 0.549→0.55, integrity of 0 and 1).
  - `parseTextShadow` / `serializeTextShadow` round-trip for `0 0 2px rgba(0,0,0,0.8)` and width 0.
  - Panel render: correct pre-fill from a legacy string config; emit produces composed `background`/`outline`.
  - Unparseable value: controls default, no overwrite unless row touched.
- Run `npm test` (Vitest). No backend changes → backend suite unaffected.

## 7. Out of scope / future

- Gradient / multi-stop background, adjustable blur/fade on outline — could reuse the same serializers later with structured fields, but intentionally not now.
- Larger outline width ceiling — base is 10px; the constant can be raised later without config changes.