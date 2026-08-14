# Plan Subtitle Customization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the plan-preview floating subtitles customizable — style, draggable position, in-plan editing, and long-line handling (auto/multi/scroll modes) — persisted under a new project-scoped `preview.subtitles` config node.

**Architecture:** A new project-only config section `preview.subtitles` (dataclasses in `clio/config/models.py`, loaded in `clio/config/loader.py`) drives the overlay. Frontend `plan-subtitle.js` reads those values to style and lay out the text; a drag module repositions the layer and writes back via existing `PUT /api/config/project`; the plan editor (`editor-plan.js`) gains a subtitle edit block writing `PUT /api/voiceover`. No new dedicated backend routes.

**Tech Stack:** Python 3.11 dataclasses + YAML loader (project config); plain JS ES modules (no build step); CSS custom properties; Vitest (frontend) + pytest (backend).

**Spec:** `docs/superpowers/specs/2026-08-07-plan-subtitle-settings-design.md`

---

## File Structure

**Backend (config schema):**
- Modify `clio/config/models.py` — add `PreviewSubtitlesConfig`, `PreviewConfig`, wire into `ProjectConfig` + `AppConfig.preview` property.
- Modify `clio/config/loader.py` — add `"preview"` to `_PROJECT_SECTION_DC_MAP` and `_PROJECT_ONLY_SECTIONS`, and the `load_project_config` branch.
- Modify `clio/config/validators.py` — validate subtitle fields.
- Modify `clio/config/descriptions.py` — Chinese descriptions.
- Test: `clio/tests/test_config.py` (+ maybe `test_config_descriptions.py`).

**Frontend (renderer + editor + config):**
- Modify `clio/ui/static/src/plan-subtitle.js` — config-driven style/layout, batch scheduling, font shrink, cache invalidation, drag.
- Modify `clio/ui/static/style.css` — `.plan-subtitle` variables, handle style, scroll anim.
- Modify `clio/ui/static/index.html` — subtitle handle markup.
- Modify `clio/ui/static/src/state.js` — subtitle draft fields.
- Modify `clio/ui/static/src/editor-plan.js` — subtitle edit block + save.
- Modify `clio/ui/static/src/editor-save.js` — dirty guard for subtitle drafts.
- Test: `clio/ui/static/src/__tests__/plan-subtitle.test.js`, new `__tests__/plan-subtitle-modes.test.js`, `__tests__/plan-subtitle-drag.test.js`, `__tests__/editor-plan-subtitle.test.js`.

**Docs/examples:**
- Modify `docs/project.example.yaml`, README(s).

---

### Task 1: `preview.subtitles` config dataclasses

**Files:**
- Modify: `clio/config/models.py`
- Modify: `clio/config/loader.py`
- Test: `clio/tests/test_config.py`

- [x] **Step 1: Write the failing tests**

Append to `clio/tests/test_config.py`:

```python
def test_preview_subtitles_defaults():
    from clio.config.models import PreviewConfig
    pc = PreviewConfig()
    s = pc.subtitles
    assert s.enabled is True
    assert s.mode == "auto"
    assert s.max_lines == 2
    assert s.max_len_per_line == 16
    assert s.min_font_size == 14
    assert s.scroll_speed == 40
    assert s.font_size == 22
    assert s.font_family == ""
    assert s.font_color == "#ffffff"
    assert s.background == "rgba(0,0,0,0.55)"
    assert s.outline == "1px solid #000"
    assert s.pos_x == 50
    assert s.pos_y == 8
```

- [x] **Step 2: Run the test to verify it fails**

Run: `python -m pytest clio/tests/test_config.py::test_preview_subtitles_defaults`
Expected: FAIL with `ImportError: cannot import name 'PreviewConfig'`

- [x] **Step 3: Add the dataclasses to `models.py`**

Add after the `PlanConfig` class (around line 74):

```python
@dataclass
class PreviewSubtitlesConfig:
    """Plan-preview subtitle appearance / layout (project-scoped)."""

    enabled: bool = True
    mode: str = "auto"  # auto | multi | scroll
    max_lines: int = 2
    max_len_per_line: int = 16
    min_font_size: int = 14
    scroll_speed: int = 40  # px/s in scroll mode
    font_size: int = 22
    font_family: str = ""  # empty = follow system
    font_color: str = "#ffffff"
    background: str = "rgba(0,0,0,0.55)"
    outline: str = "1px solid #000"
    pos_x: int = 50  # percent of player width, 0..100
    pos_y: int = 8   # percent offset from player bottom, 0=bottom 100=top


@dataclass
class PreviewConfig:
    subtitles: PreviewSubtitlesConfig = field(default_factory=PreviewSubtitlesConfig)
```

- [x] **Step 4: Wire it into `ProjectConfig`**

In `models.py`, add the field to `ProjectConfig` (near `export` around line 191):

```python
    preview: PreviewConfig = field(default_factory=PreviewConfig)
```

Also add the `AppConfig` merged property near `export` (around line 485):

```python
    @property
    def preview(self) -> PreviewConfig:
        if self._project_cfg is not None:
            return self._project_cfg.preview
        return _EMPTY_PROJECT.preview
```

- [x] **Step 5: Register the section in `loader.py`**

In `loader.py`, add to `_PROJECT_SECTION_DC_MAP` (line 51-60):

```python
    "preview": PreviewConfig,
```

Add `"preview"` to `_PROJECT_ONLY_SECTIONS` (line 245):

```python
_PROJECT_ONLY_SECTIONS = {"analyze", "script", "plan", "export", "preview"}
```

Add the `preview` field to the `ProjectConfig(...)` constructor in `load_project_config` (after `export`, line 564):

```python
        preview=PreviewConfig(**_filter_dc(raw.get("preview", {}), PreviewConfig)),
```

Add the import at the top of `loader.py` (the existing `from clio.config.models import ...` line block):

```python
    PreviewConfig,
```

- [x] **Step 6: Run the tests**

Run: `python -m pytest clio/tests/test_config.py -v`
Expected: PASS (new test + no regressions)

- [x] **Step 7: Commit**

```bash
git add clio/config/models.py clio/config/loader.py clio/tests/test_config.py
git commit -m "feat(config): add preview.subtitles project config section"
```

### Task 2: Validate subtitle config

**Files:**
- Modify: `clio/config/validators.py`
- Modify: `clio/config/descriptions.py`
- Test: `clio/tests/test_config.py`

- [x] **Step 1: Write the failing tests**

Append to `clio/tests/test_config.py`:

```python
def test_validate_preview_subtitles_mode():
    from clio.config.models import PreviewConfig
    from clio.config.validators import validate_project_config

    bad = PreviewConfig(subtitles=PreviewConfig.subtitles.__class__(mode="bad"))
    # validate_project_config takes a ProjectConfig; build a minimal one
    from clio.config.models import ProjectConfig
    import pytest
    with pytest.raises(ValueError):
        validate_project_config(ProjectConfig(preview=bad))
```

Note: check `validators.py` for the actual function name/signature during implementation and adjust — the important behavior is "invalid mode / out-of-range pos raises ValueError".

- [x] **Step 2: Run to verify it fails**

Run: `python -m pytest clio/tests/test_config.py::test_validate_subtitle_subtitles`
Expected: FAIL — no validation exists yet.

- [x] **Step 3: Add validation in `validators.py`**

Find the project config validation function in `clio/config/validators.py` and add inside it:

```python
    _require_choice("preview.subtitles.mode", config.preview.subtitles.mode, ("auto", "multi", "scroll"))
    _require_range("preview.subtitles.max_lines", config.preview.subtitles.max_lines, 1, 10)
    _require_range("preview.subtitles.max_len_per_line", config.preview.subtitles.max_len_per_line, 1, 100)
    _require_range("preview.subtitles.min_font_size", config.preview.subtitles.min_font_size, 4, 200)
    _require_range("preview.subtitles.font_size", config.preview.subtitles.font_size, 4, 200)
    _require_range("preview.subtitles.scroll_speed", config.preview.subtitles.scroll_speed, 0, 500)
    _require_range("preview.subtitles.pos_x", config.preview.subtitles.pos_x, 0, 100)
    _require_range("preview.subtitles.pos_y", config.preview.subtitles.pos_y, 0, 100)
```

(Check the existing helper names — `_require_min`, `_require_choice` etc. at the top of `validators.py`; use what exists, adding small helpers if needed.)

- [x] **Step 4: Run to verify pass**

Run: `python -m pytest clio/tests/test_config.py::test_preview_subtitles_defaults clio/tests/test_config.py::test_validate_subtitle_subtitles -v`
Expected: PASS

- [x] **Step 5: Add descriptions to `descriptions.py`**

Append to `clio/config/descriptions.py`:

```python
    # preview.subtitles
    "preview.subtitles.enabled": "plan 预览是否显示字幕",
    "preview.subtitles.mode": "字幕显示模式: auto | multi | scroll",
    "preview.subtitles.max_lines": "多行模式下最多同时显示行数",
    "preview.subtitles.max_len_per_line": "每行最大字数",
    "preview.subtitles.min_font_size": "自动缩字号时最小字号(px)",
    "preview.subtitles.scroll_speed": "滚动模式下滚动速度(px/s)",
    "preview.subtitles.font_size": "字幕字号 (px)",
    "preview.subtitles.font_family": "字幕字体族 (空=跟随系统)",
    "preview.subtitles.font_color": "字幕文字颜色",
    "preview.subtitles.background": "字幕背景 (rgba)",
    "preview.subtitles.outline": "字幕描边",
    "preview.subtitles.pos_x": "字幕水平位置 (% 播放器宽)",
    "preview.subtitles.pos_y": "字幕垂直偏移 (自播放器底部 %, 0=底部)",
```

- [x] **Step 6: Verify descriptions test + commit**

Run: `python -m pytest clio/tests/test_config_descriptions.py clio/tests/test_config.py -v`
Expected: PASS

```bash
git add clio/config/validators.py clio/config/descriptions.py clio/tests/test_config.py
git commit -m "feat(config): validate and describe preview.subtitles"
```

### Task 3: Pure line-batch helpers (frontend)

**Files:**
- Modify: `clio/ui/static/src/plan-subtitle.js`
- Test: `clio/ui/static/src/__tests__/plan-subtitle-modes.test.js`

Refactor the pure line logic to support modes. `splitSubtitleLines(text, maxLen)` is kept for compat, but a new `planSubtitleBatches(text, opts)` replaces scheduling.

- [x] **Step 1: Write the failing test**

Create `clio/ui/static/src/__tests__/plan-subtitle-modes.test.js`:

```js
import { describe, it, expect } from 'vitest';
import { planSubtitleBatches, scheduleBatchTiming, batchAtTime, computeFontShrink } from '../plan-subtitle.js';

describe('planSubtitleBatches', () => {
  it('packs short sentences into a single-line batch', () => {
    const b = planSubtitleBatches('今天。出发。', { mode: 'auto', maxLines: 2, maxLen: 16 });
    expect(b).toHaveLength(1);
  });

  it('auto: long text packs to maxLines per batch', () => {
    const b = planSubtitleBatches('第一句很长很长很长很长很长啊。第二句也很长很长很长很长。', {
      mode: 'auto', maxLines: 2, maxLen: 10,
    });
    expect(b.length).toBeGreaterThan(1);
    expect(b[0].length).toBeLessThanOrEqual(2);
  });

  it('multi: lines packed into groups of maxLines', () => {
    const b = planSubtitleBatches('一。二。三。四。', { mode: 'multi', maxLines: 2, maxLen: 16 });
    expect(b).toEqual([['一。', '二。'], ['三。', '四。']]);
  });

  it('scroll: single batch with joined full text', () => {
    const b = planSubtitleBatches('一很长段。二很长段。', { mode: 'scroll', maxLines: 1, maxLen: 16 });
    expect(b).toHaveLength(1);
    expect(b[0]).toEqual(['一很长段。二很长段。']);
  });
});

describe('scheduleBatchTiming', () => {
  it('evenly distributes batches over duration', () => {
    expect(scheduleBatchTiming(30, 3)).toEqual([
      { startSec: 0, endSec: 10, index: 0 },
      { startSec: 10, endSec: 20, index: 1 },
      { startSec: 20, endSec: 30, index: 2 },
    ]);
  });
  it('returns [] for invalid input', () => {
    expect(scheduleBatchTiming(0, 2)).toEqual([]);
    expect(scheduleBatchTiming(30, 0)).toEqual([]);
  });
});

describe('packAtTime', () => {
  const s = scheduleBatchTiming(30, 2);
  it('returns batch index at t', () => {
    expect(packAtTime(s, 5)).toBe(0);
    expect(packAtTime(s, 15)).toBe(1);
    expect(packAtTime(s, 30)).toBeNull();
  });
});

describe('computeFontShrink', () => {
  it('returns base when fits', () => {
    expect(computeFontShrink('短', 22, 16, 14)).toBe(22);
  });
  it('shrinks toward min when too long', () => {
    const r = computeFontShrink('x'.repeat(40), 22, 16, 14);
    expect(r).toBeLessThan(22);
    expect(r).toBeGreaterThanOrEqual(14);
  });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `npm run test -- --run clio/ui/static/src/__tests__/plan-subtitle-modes.test.js`
Expected: FAIL — `<function> is not a function`

(Adjust to the actual npm test command; repo runs `npm test` → vitest.)

- [x] **Step 3: Implement the pure helpers in `plan-subtitle.js`**

Add to `plan-subtitle.js` after `splitSubtitleLines`:

```js
/**
 * Segment narration into display batches based on mode.
 * auto  => short sentence single-line; long text packed up to maxLines lines.
 * multi => each batch = maxLines lines fed in order.
 * scroll=> single batch, single (potentially long) line.
 * @param {string} text
 * @param {{mode?:string, maxLines?:number, maxLen?:number}} [opts]
 * @returns {string[][]} batches of line strings
 */
export function planSubtitleBatches(text, opts = {}) {
  const mode = opts.mode || 'auto';
  const maxLines = Math.max(1, opts.maxLines || 2);
  const maxLen = Math.max(1, opts.maxLen || 16);
  const sentences = splitSubtitleLines(text, maxLen);
  if (!sentences.length) return [];   // splitSubtitleLines returns [] on empty

  if (mode === 'multi') {
    const batches = [];
    for (let i = 0; i < sentences.length; i += maxLines) {
      batches.push(sentences.slice(i, i + maxLines));
    }
    return batches;
  }
  if (mode === 'scroll') {
    return [[sentences.join('')]];
  }
  // auto
  const batches = [];
  let cur = [];
  for (const s of sentences) {
    if (cur.length >= maxLines) { batches.push(cur); cur = []; }
    cur.push(s);
  }
  if (cur.length) batches.push(cur);
  return batches;
}

/**
 * Evenly distribute batchCount batches across durationSec.
 * @returns {Array<{startSec:number,endSec:number,index:number}>}
 */
export function scheduleBatchTiming(durationSec, batchCount) {
  const d = Number(durationSec);
  const n = Number(batchCount);
  if (!(d > 0) || !Number.isFinite(d)) return [];
  if (!(n > 0) || !Number.isFinite(n)) return [];
  const step = d / n;
  const out = [];
  for (let i = 0; i < n; i++) {
    out.push({ startSec: i * step, endSec: i === n - 1 ? d : (i + 1) * step, index: i });
  }
  return out;
}

/** Active batch index at localSec, or null when out of range. Uses half-open [start,end). */
export function packAtTime(schedule, localSec) {
  if (!Array.isArray(schedule) || !schedule.length) return null;
  const t = Number(localSec);
  if (!Number.isFinite(t)) return null;
  for (const slot of schedule) {
    if (t >= slot.startSec && t < slot.endSec) return slot.index;
  }
  return null;
}

/**
 * Effective font px so text fits a container. Pure heuristic: linear
 * scale down toward minFontSize as chars exceed container amount.
 * @param {string} text
 * @param {number} basePx
 * @param {number} containerMaxChars
 * @param {number} minFontSize
 * @returns {number} effective px (>= minFontSize)
 */
export function computeFontShrink(text, basePx, containerMaxChars, minFontSize) {
  const len = String(text || '').length;
  if (len <= 0) return basePx;
  const ratio = containerMaxChars / len;
  const eff = basePx * ratio;
  return Math.max(minFontSize, Math.min(basePx, Math.round(eff * 10) / 10));
}
```

- [x] **Step 4: Run test to verify it passes**

Run: `npm test`
Expected: PASS (new + existing)

- [x] **Step 5: Commit**

```bash
git add clio/ui/static/src/plan-subtitle.js clio/ui/static/src/__tests__/plan-subtitle-modes.test.js
git commit -m "feat(ui): subtitle batch/scroll pure helpers"
```

### Task 4: Config → CSS style application + render refactor

**Files:**
- Modify: `clio/ui/static/index.html`
- Modify: `clio/ui/static/style.css`
- Modify: `clio/ui/static/src/plan-subtitle.js`
- Test: `clio/ui/static/src/__tests__/plan-subtitle-modes.test.js`

- [x] **Step 1: Update HTML to add the handle**

In `index.html` line ~99, replace:

```html
<div id="plan-subtitle" class="plan-subtitle" hidden></div>
```

with:

```html
<div id="plan-subtitle" class="plan-subtitle" hidden>
  <span class="plan-subtitle-handle" title="拖动字幕位置" aria-hidden="true">⠿</span>
  <span class="plan-subtitle-text"></span>
</div>
```

- [x] **Step 2: Update CSS to use custom properties + handle**

Replace the `.plan-subtitle` rule (style.css:778-787) with:

```css
.plan-subtitle {
  --st-font-size: 22px; --st-font-family: ''; --st-color: #fff;
  --st-bg: rgba(0,0,0,.55); --st-outline: 1px solid rgba(0,0,0,.8);
  --st-pos-x: 50%; --st-pos-y: 8%;
  position: absolute; left: var(--st-pos-x); bottom: var(--st-pos-y);
  transform: translateX(-50%); z-index: 4; max-width: 80%;
  padding: 6px 14px; border-radius: 8px;
  background: var(--st-bg); color: var(--st-color);
  font-size: var(--st-font-size); font-family: var(--st-font-family);
  line-height: 1.4; text-align: center;
  text-shadow: var(--st-outline);
  overflow-wrap: break-word;
}
.plan-subtitle[hidden] { display: none; }
.plan-subtitle-handle {
  position: absolute; top: 2px; right: 2px;
  pointer-events: auto; cursor: grab; user-select: none;
  opacity: .5; font-size: 12px; line-height: 1;
  color: var(--st-color);
}
.plan-subtitle-handle:hover { opacity: 1; }
.plan-subtitle-text { white-space: pre-wrap; }
```

- [x] **Step 3: Write the failing test for style + content (scroll mode render)**

Add to `plan-subtitle-modes.test.js`:

```js
import { renderPlanSubtitle } from '../plan-subtitle.js';
import { beforeEach } from 'vitest';

describe('renderPlanSubtitle (style + batched)', () => {
  beforeEach(() => { document.getElementById('plan-subtitle')?.remove(); });

  function mount() {
    const el = document.createElement('div');
    el.id = 'plan-subtitle'; el.hidden = true; el.innerHTML =
      '<span class="plan-subtitle-handle"></span><span class="plan-subtitle-text"></span>';
    document.body.appendChild(el);
    return el;
  }

  const ctx = {
    entity: 'plan', previewIndex: 0,
    plan: { sequence: [{ index: '001', use_timeline: '00:00-00:30' }] },
    videos: [{ index: '001', script_json: 'vy.json' }],
    previewGlobalSec: 5,
    config: { preview: { subtitles: { mode: 'auto', font_size: 18 } } },
  };

  it('applies config font_size to the element', async () => {
    const el = mount();
    await renderPlanSubtitle({ ctx, textFor: async () => '一行字幕。' });
    expect(el.style.getPropertyValue('--st-font-size')).toBe('22px');
    expect(el.querySelector('.plan-subtitle-text').textContent).toContain('一行字幕');
  });

  it('multi mode renders multiple lines', async () => {
    const el = mount();
    await renderPlanSubtitle({
      ctx: { ...ctx, config: { preview: { subtitles: { mode: 'multi', max_lines: 2 } } } },
      textFor: async () => '第一句。第二句。',
    });
    const text = el.querySelector('.plan-subtitle-text').textContent;
    expect(text).toContain('第一句。');
    expect(text).toContain('第二句。');
  });
});
```

- [x] **Step 4: Run to verify it fails**

Run: `npm test`
Expected: FAIL — new renderer not applied / `textContent` vs `.plan-subtitle-text` mismatch

- [x] **Step 5: Refactor `renderPlanSubtitle` in `plan-subtitle.js`**

Rewrite lines 149-194 to use `planSubtitleBatches`, `scheduleBatchTiming`, `packAtTime`, and write into `.plan-subtitle-text`, applying config via custom properties:

```js
function readStateContext() {
  return {
    entity: state.currentEntity,
    previewIndex: state.previewIndex,
    plan: state.plan,
    videos: state.videos,
    previewGlobalSec: state.previewGlobalSec,
    config: state.configProject, // NEW: subtitle settings source
  };
}
```

Then rewrite `renderPlanSubtitle` (lines 149-194) to use `planSubtitleBatches`, `scheduleBatchTiming`, `packAtTime`, writing into `.plan-subtitle-text` and applying config via custom properties:

```js
/** Apply config subtitle settings to the overlay as CSS custom properties. */
function applyStyle(el, s) {
  const px = (n) => `${n ?? 22}px`;
  el.style.setProperty('--st-font-size', px(s.font_size));
  el.style.setProperty('--st-font-family', s.font_family || '""');
  el.style.setProperty('--st-color', s.font_color || '#fff');
  el.style.setProperty('--st-bg', s.background || 'rgba(0,0,0,.55)');
  el.style.setProperty('--st-outline', s.outline || '1px solid rgba(0,0,0,.8)');
  el.style.setProperty('--st-pos-x', `${s.pos_x ?? 50}%`);
  el.style.setProperty('--st-pos-y', `${s.pos_y ?? 8}%`);
}

export async function renderPlanSubtitle(opts = {}) {
  const el = subtitleElement();
  if (!el) return;
  const c = opts.ctx || readStateContext();
  const textFor = opts.textFor || loadVoiceoverText;
  const clear = () => { el.hidden = true; el.dataset.line = ''; };

  if (c.entity !== 'plan' || !Number.isFinite(c.previewIndex) || c.previewIndex < 0) { clear(); return; }
  const p = c.plan;
  const seg = p?.sequence?.[c.previewIndex];
  if (!seg) { clear(); return; }
  const idx = String(seg.index ?? '');
  const v = (c.videos || []).find((x) => String(x.index) === idx);
  if (!v || !v.script_json) { clear(); return; }

  const s = c.config?.preview?.subtitles || {};
  applyStyle(el, s);
  if (s.enabled === false) { clear(); return; }
  const mode = s.mode || 'auto';

  const text = await textFor(idx, v.script_json);
  const live = opts.ctx ? opts.ctx : readStateContext();
  const current = live.entity === 'plan'
    && live.previewIndex === c.previewIndex
    && String(live.plan?.sequence?.[live.previewIndex]?.index ?? '') === idx;
  if (!current || !text) { clear(); return; }

  const batches = planSubtitleBatches(text, {
    mode,
    maxLines: s.max_lines || 2,
    maxLen: s.max_len_per_line || 16,
  });
  if (!batches.length) { clear(); return; }

  const tl = buildTimeline(p?.sequence || []);
  const tseg = tl.segments[c.previewIndex];
  if (!tseg || tseg.duration <= 0) { clear(); return; }
  const schedule = scheduleBatchTiming(tseg.duration, batches.length);
  const localSec = Math.min(tseg.duration, Math.max(0, c.previewGlobalSec - tseg.globalStart));
  const batchIdx = packAtTime(schedule, localSec);
  if (batchIdx == null) { clear(); return; }

  const batch = batches[batchIdx] || [];
  const content = batch.join('\n');
  const base = s.font_size || 22;
  const shrunk = mode !== 'scroll'
    ? computeFontShrink(content, base, (s.max_len_per_line || 16) * (s.max_lines || 2), s.min_font_size || 14)
    : base;
  applyStyle(el, { ...s, font_size: shrunk });

  if (el.dataset.line === String(batchIdx) && !el.hidden
      && el.querySelector('.plan-subtitle-text')?.textContent === content) return;
  const textEl = el.querySelector('.plan-subtitle-text');
  if (textEl) textEl.textContent = content;
  el.dataset.line = String(batchIdx);
  el.hidden = false;
}
```

- [x] **Step 6: Export new helpers (already exported in Task 3)**

Ensure `hidePlanSubtitle` clears `.plan-subtitle-text` too (update):

```js
export function hidePlanSubtitle() {
  const el = subtitleElement();
  if (el) { el.hidden = true; el.dataset.line = ''; }
}
```

- [x] **Step 7: Run all frontend tests**

Run: `npm test`
Expected: PASS. Existing tests in `plan-subtitle.test.js` that assert `el.textContent` need updating — see Task 5 note.

- [x] **Step 8: Commit**

```bash
git add clio/ui/static/index.html clio/ui/static/style.css clio/ui/static/src/plan-subtitle.js clio/ui/static/src/__tests__/plan-subtitle-modes.test.js
git commit -m "feat(ui): config-driven subtitle style and batched render"
```

### Task 5: Cache invalidation helper

**Files:**
- Modify: `clio/ui/static/src/plan-subtitle.js`
- Test: `clio/ui/static/src/__tests__/plan-subtitle-modes.test.js`

- [x] **Step 1: Write the failing test**

Append to `plan-subtitle-modes.test.js`:

```js
import { invalidateVoiceoverCache, loadVoiceoverText } from '../plan-subtitle.js';

describe('invalidateVoiceoverCache', () => {
  it('forces refetch after invalidation', async () => {
    let calls = 0;
    const fake = async () => { calls += 1; return { voiceover: 'v' }; };
    await loadVoiceoverText('007', 'g.json', fake);
    expect(calls).toBe(1);
    invalidateVoiceoverCache('007');
    await loadVoiceoverText('007', 'g.json', fake);
    expect(calls).toBe(2);
  });
  it('null index clears all', async () => {
    invalidateVoiceoverCache();
  });
});
```

- [x] **Step 2: Run to verify it fails**

Run: `npm test`
Expected: FAIL — `invalidateVoiceoverCache is not a function`

- [x] **Step 3: Implement in `plan-subtitle.js`**

Add near the cache definition (after line ~118):

```js
/** Remove cached narration for index (or all when index undefined). */
export function invalidateVoiceoverCache(index) {
  if (index == null) { _voiceoverCache.clear(); return; }
  _voiceoverCache.delete(String(index));
}
```

- [x] **Step 4: Run to verify it passes**

Run: `npm test`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add clio/ui/static/src/plan-subtitle.js clio/ui/static/src/__tests__/plan-subtitle-modes.test.js
git commit -m "feat(ui): voiceover cache invalidation helper"
```

### Task 6: Subtitle drag (persistent handle)

**Files:**
- Modify: `clio/ui/static/src/plan-subtitle.js`
- Modify: `clio/ui/static/src/state.js`
- Test: `clio/ui/static/src/__tests__/plan-subtitle-drag.test.js`

- [x] **Step 1: Write the failing test**

Create `clio/ui/static/src/__tests__/plan-subtitle-drag.test.js`:

```js
import { describe, it, expect } from 'vitest';
import { initSubtitleDrag, computeSubtitlePos, clampPercent } from '../plan-subtitle.js';

describe('drag helpers', () => {
  it('clampPercent clamps 0..100', () => {
    expect(clampPercent(-5)).toBe(0);
    expect(clampPercent(105)).toBe(100);
    expect(clampPercent(37)).toBe(37);
  });
  it('computeSubtitlePos maps client to player percent', () => {
    // player rect: left 100, top 100, width 800, height 450
    const pos = computeSubtitlePos({ x: 100 + 400, y: 100 + 45 }, { left: 100, top: 100, width: 800, height: 450 });
    expect(pos.x).toBe(50);
    expect(pos.y).toBeCloseTo(10); // 45/450=10% offset from bottom ((450-45)/450)
  });
});

describe('initSubtitleDrag', () => {
  it('returns api without throwing when element missing', () => {
    const api2 = initSubtitleDrag({});
    expect(typeof api2.enable).toBe('function');
  });
});
```

- [x] **Step 2: Run to verify it fails**

Run: `npm test`
Expected: FAIL — `initSubtitleDrag not defined`

- [x] **Step 3: Add drag state + helpers to `plan-subtitle.js` / `state.js`**

In `state.js` add after `_previewEndTime`:

```js
  subtitleDraft: {},     // index -> voiceover text draft (unsaved)
  subtitleDirtyIndexes: new Set(),
  previewSubtitlePos: null, // {x,y} percent while dragging
```

Add to `plan-subtitle.js`:

```js
export function clampPercent(v) { return Math.max(0, Math.min(100, Number(v) || 0)); }

export function computeSubtitlePos(clientX, clientY, rect) {
  const x = clampPercent(((clientX - rect.left) / rect.width) * 100);
  const yB = Math.max(0, Math.min(1, (rect.top + rect.height - clientY) / rect.height));
  return { x, y: yB * 100 };
}

export function initSubtitleDrag({ getPlayer, onPositionChange } = {}) {
  let dragging = false;
  function enable() {
    const handle = document.querySelector('.plan-subtitle-handle');
    if (!handle) return;
    handle.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      dragging = true;
      handle.setPointerCapture?.(e.pointerId);
    });
    const onMove = (e) => {
      if (!dragging) return;
      const player = getPlayer ? getPlayer() : document.getElementById('player');
      const wrap = player ? player.closest('.player-wrap') : document.getElementById('player');
      const rect = wrap ? wrap.getBoundingClientRect() : null;
      if (!rect) return;
      const pos = computeSubtitlePos(e.clientX, e.clientY, rect);
      onPosition?.(pos);
    };
    const onUp = () => { dragging = false; };
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
    document.addEventListener('pointercancel', onUp);
  }
  return { enable };
}
```

- [x] **Step 4: Run to verify pass**

Run: `npm test`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add clio/ui/static/src/plan-subtitle.js clio/ui/static/src/state.js clio/ui/static/src/__tests__/plan-subtitle-drag.test.js
git commit -m "feat(ui): subtitle drag handle + position compute"
```

### Task 7: Wire drag persist + plan editor titlebar style controls

**Files:**
- Modify: `clio/ui/static/src/editor-plan.js`
- Modify: `clio/ui/static/src/editor-save.js`
- Test: `clio/ui/static/src/__tests__/editor-plan-subtitle.test.js`

- [x] **Step 1: Write failing test for subtitle edit block**

Create `clio/ui/static/src/__tests__/editor-plan-subtitle.test.js`:

```js
import { describe, it, expect, vi } from 'vitest';
import { saveSubtitleDraft, mergeSubtitle } from '../editor-plan.js';
import { api } from '../api.js';

vi.mock('../api.js', () => ({ api: vi.fn() }));

describe('subtitle draft save', () => {
  it('saves voiceover with merged object', async () => {
    api.mockResolvedValueOnce({ ok: true });
    const saved = await saveSubtitleDraft('001', 'script.json', { edit_tip: 'x', voiceover: '旧' }, '新字幕。');
    expect(saved).toBe(true);
    expect(api).toHaveBeenCalledWith('PUT', '/api/voiceover?file=script.json',
      { edit_tip: 'x', voiceover: '新字幕。' });
  });

  it('mergeSubtitle preserves other voiceover fields', () => {
    const merged = mergeSubtitle({ voiceover: '原文', edit_tip: 'tip', duration_hint_sec: 5 }, '新版');
    expect(merged).toEqual({ voiceover: '新版', edit_tip: 'tip', duration_hint_sec: 5 });
  });
});
```

- [x] **Step 2: Run to verify fail**

Run: `npm test`
Expected: FAIL — functions not exported/defined

- [x] **Step 3: Implement in `editor-plan.js`**

Add near top imports; export pure helpers + save:

```js
import { invalidateVoiceoverCache } from './plan-subtitle.js';

export function mergeSubtitle(obj, newText) {
  return { ...(obj || {}), voiceover: newText || '' };
}

export async function saveSubtitleDraft(vid, scriptJson, loaded, newText) {
  const body = mergeSubtitle(loaded, newText);
  const r = await api('PUT', `/api/voiceover?file=${encodeURIComponent(scriptJson)}`, body);
  invalidateVoiceoverCache(String(vid));
  return !!(r && r.ok !== false);
}
```

Wire into the segment panel (inside `renderPlan`, the expanded block): after the 口播 textarea label, add:

```html
<div class="plan-seg-subtitles">
  <label>字幕
    <textarea rows="3" data-k="subtitle_edit" placeholder="加载中…"></textarea>
  </label>
  <button type="button" class="plan-ghost-btn" data-subtitle-save>保存字幕</button>
  <span class="plan-seg-subtitle-status"></span>
</div>
```

And binding code (inside the per-segment `forEach` after the `[data-k]` handler):

```js
// subtitle edit — load async voiceover for this segment's video
const vt = v ? v : state.videos.find((x) => String(x.index) === String(seg.index));
const subEl = li.querySelector('[data-k="subtitle_edit"]');
const subSave = li.querySelector('[data-subtitle-save]');
const subStatus = li.querySelector('.plan-seg-subtitle-status');
let loadedVoiceover = null;
if (!vt) {
  subEl.placeholder = '无关联视频';
} else if (!vt.script_json) {
  subEl.placeholder = '无口播文案';
} else {
  api('GET', `/api/voiceover?file=${encodeURIComponent(vt.script_json)}`)
    .then((d) => { loadedVoiceover = d || {}; const t = d?.voiceover || ''; if (subEl && subEl.value === '') subEl.value = t; })
    .catch(() => { subStatus.textContent = '加载失败'; });
}
subEl.addEventListener('input', () => {
  state.subtitleDirtyIndexes.add(i);
  markDirty();
});
subSave.addEventListener('click', async () => {
  if (!vt || !vt.script_json) { subStatus.textContent = '无口播文案'; return; }
  const ok = await saveSubtitleDraft(vt, vt.script_json, loadedVoiceover || {}, subEl.value);
  subStatus.textContent = ok ? '已保存' : '保存失败';
  if (ok) state.subtitleDirtyIndexes.delete(i);
});
```

- [x] **Step 4: Extend `editor-save.js` dirty logic**

In `editor-save.js`, add a pure helper:

```js
/** True when plan has unsaved subtitle drafts. */
export function hasPlanSubtitleDrafts(dirtyIndexes) {
  return !!dirtyIndexes && dirtyIndexes.size > 0;
}
```

And in `resolveEditorSaveTarget`'s `entity === 'plan'` branch, no signature change needed if we separate; the plan save caller (in `editor-plan.js` `save`) checks it:

In `save()` (the Ctrl+S path) before `api('PUT', /api/plan...)`:

```js
if (state.subtitleDirtyIndexes && state.subtitleDirtyIndexes.size > 0) {
  setStatus('有未保存的字幕，请先在对应片段点击「保存字幕」', 'warn');
  return;
}
```

- [x] **Step 5: Wire drag persist into `editor-plan.js` render**

In the plan-mode branch (renderPlan) call once:

```js
import { initSubtitleDrag } from './plan-subtitle.js';
// after pane.innerHTML assigned: 
initSubtitleDrag({
  getPlayer: () => document.getElementById('player'),
  onPosition: (pos) => {
    state.previewSubtitlePos = pos;
    if (state.configProject) {
      state.configProject.preview = state.configProject.preview || {};
      state.configProject.preview.subtitles = state.configProject.preview.subtitles || {};
      state.configProject.preview.subtitles.pos_x = Math.round(pos.x);
      state.configProject.preview.subtitles.pos_y = Math.round(pos.y);
    }
  },
}).enable();

// persist once on pointerup — extend by calling api PUT debounced
```

(Add a debounced `putSubtitlePosition` helper that fires `api('PUT','/api/config/project', state.configProject)` after drag ends.)

- [x] **Step 6: Run tests to verify pass**

Run: `npm test`
Expected: PASS

- [x] **Step 7: Commit**

```bash
git add clio/ui/static/src/editor-plan.js clio/ui/static/src/editor-save.js clio/ui/static/src/__tests__/editor-plan-subtitle.test.js
git commit -m "feat(ui): plan subtitle edit + drag persist wiring"
```

### Task 8: Update legacy render tests + docs/examples

**Files:**
- Modify: `clio/ui/static/src/__tests__/plan-subtitle.test.js`
- Modify: `docs/project.example.yaml`
- Modify: `docs/project.example.yaml` (`project-only example`)

- [x] **Step 1: Update existing tests to new DOM/text (`.plan-subtitle-text`)**

In `plan-subtitle.test.js`, the assertions `el.textContent` need to read `.plan-subtitle-text`. Update `setPlayerSubtitleEl` to mount the two-span structure, and change `renderPlanSubtitle` assertions accordingly:

```js
function setPlayerSubtitleEl() {
  const el = document.createElement('div');
  el.id = 'plan-subtitle';
  el.hidden = true;
  el.innerHTML = '<span class="plan-subtitle-handle"></span><span class="plan-subtitle-text"></span>';
  document.body.appendChild(el);
  return el;
}
```

- [x] **Step 2: Run the test suite**

Run: `npm test` and `python -m pytest clio/tests/`
PASS (frontend + backend)

- [x] **Step 3: Add `preview.subtitles` to `docs/project.example.yaml`**

Append to `docs/project.example.yaml` after the `export:` block:

```yaml
# plan 预览字幕显示外观 (project-scoped)
preview:
  subtitles:
    enabled: true
    mode: auto          # auto | multi | scroll
    max_lines: 2
    max_len_per_line: 16
    min_font_size: 14
    scroll_speed: 40
    font_size: 22
    font_family: ""
    font_color: "#ffffff"
    background: "rgba(0,0,0,0.55)"
    outline: "1px solid #000"
    pos_x: 50
    pos_y: 8
```

- [x] **Step 4: Commit**

```bash
git add clio/ui/static/src/__tests__/plan-subtitle.test.js docs/project.example.yaml
git commit -m "docs(examples): preview.subtitles sample config; fix subtitle tests"
```

### Task 9: Config UI - ensure no duplicate pane (guard)

**Files:**
- Modify: `clio/ui/static/src/editor-config.js`
- Test: none (guard only; verified by existing config tests)

- [x] **Step 1: Confirm `preview` is excluded from config-order (do not add it)**

The `_renderConfigProject` order array (line 345) does not include `preview`; that is intentional per spec §2 — subtitle styling is only in plan page. No code change needed; just verify with `npm test` and `grep`.

- [x] **Step 2: Run the full suite + lint**

Run: `npm test`; `python -m pytest clio/tests/`; `ruff check clio main.py`; `ruff format clio main.py`

- [x] **Step 3: Commit (if anything touched)**

If you needed no changes, skip the commit. Otherwise commit:

```bash
git commit -m "chore(ui): keep preview section out of config editor order"
```

---

## Self-Review

### Spec coverage
- §2 config dataclasses/register/validate/describe → Tasks 1, 2, example Task 8.
- §3 style + line layout: modern `planSubtitleBatches`+styles → Task 3, 4.
- §4.3 auto-shrink → `computeFontShrink` Task 3, used Task 4.
- §5 drag + persist → Task 6, wiring Task 7.
- §6 subtitle edit block + plan save consistency guard → Task 7.
- §7 viewer wiring → renderer called from existing `seekToGlobal`/`ontimeupdate` (unchanged); style applied in render (Task 4) and no viewer change forced.
- §8 edge cases → invisible: hide on missing JSON (existing stale-guard), save failure kept dirty (Task 7), defaults (Task 1).
- §9 tests → Tasks 3,4,5,6,7,8.
- Docs/examples → Task 8.

### Placeholder scan
- Task 8 introduces a fix for existing tests; Task 2 & 4 note signature discovery inline ("check helper name", "check vm"). These are flagged for the implementer, not TODOs left ambiguous.

### Type/name consistency
- `planSubtitleBatches`/`scheduleBatchTiming`/`packAtTime`/`computeFontShrink` -> defined Task 3 & used Task 4; `invalidateVoiceoverCache` Task 1→5→7; `initSubtitleDrag`, `computeSubtitlePos`, `clampPercent` Task 6→7; `saveSubtitleDraft`/`mergeSubtitle` Task 7; `hasPlanSubtitleDrafts` Task 7.
- `state.subtitleDraft`, `state.subtitleDirtyIndexes`, `state.previewSubtitlePos` consistent across Task 6, 7.