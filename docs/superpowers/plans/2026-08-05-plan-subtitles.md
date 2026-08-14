# Plan-Preview Floating Subtitles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Overlay the current plan segment's spoken `voiceover` text as a floating subtitle over the main video when previewing in plan mode.

**Architecture:** Pure-frontend. A new module `plan-subtitle.js` provides pure helpers (`splitSubtitleLines`, `scheduleSubtitleTiming`, `subtitleIndexAtTime`) plus a loader (`loadVoiceoverText`, cached per video index) and a DOM renderer. `viewer.js` calls the renderer from its existing `seekToGlobal` + `ontimeupdate` paths; a new absolutely-positioned div sits over the player. No backend or plan-file changes.

**Tech Stack:** Vanilla ES modules (no build step), jsdom Vitest.

**Spec:** `docs/superpowers/specs/2026-08-05-plan-subtitles-design.md`

---

### Task 1: Pure helper `splitSubtitleLines`

**Files:**
- Create: `clio/ui/static/src/plan-subtitle.js`
- Test: `clio/ui/static/src/__tests__/plan-subtitle.test.js`

- [x] **Step 1: Write the failing test**

```js
// clio/ui/static/src/__tests__/plan-subtitle.test.js
import { describe, it, expect } from 'vitest';
import { splitSubtitleLines } from '../plan-subtitle.js';

describe('splitSubtitleLines', () => {
  it('splits on Chinese sentence punctuation', () => {
    const lines = splitSubtitleLines('今天天气真好。我们出发吧！去海边。', 16);
    expect(lines).toEqual(['今天天气真好。', '我们出发吧！', '去海边。']);
  });

  it('splits long lines exceeding maxLen', () => {
    const long = '这是一个非常非常非常非常非常非常长的中文句子用来测试换行逻辑';
    const lines = splitSubtitleLines(long, 10);
    expect(lines.length).toBeGreaterThan(1);
    lines.forEach((l) => expect(l.length).toBeLessThanOrEqual(14)); // maxLen + punctuation carryover
  });

  it('splits on newlines too', () => {
    const lines = splitSubtitleLines('第一条\n第二条。', 16);
    expect(lines).toEqual(['第一条', '第二条。']);
  });

  it('empty / whitespace input → []', () => {
    expect(splitSubtitleLines('', 16)).toEqual([]);
    expect(splitSubtitleLines('   ', 16)).toEqual([]);
  });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `npx vitest run clio/ui/static/src/__tests__/plan-subtitle.test.js -t splitSubtitleLines`
Note: module `plan-subtitle.js` does not exist yet → FAIL.

- [x] **Step 3: Write minimal implementation**

```js
// clio/ui/static/src/plan-subtitle.js
// Pure helpers for plan-preview floating subtitles. No DOM.

const SENTENCE_BREAKS = '。！？；…!?;';
const BREAK_SET = new Set(SENTENCE_BREAKS.split(''));
const MAX_PLUS_CARRY = 4; // allow punctuation to overflow maxLen slightly

/**
 * Split narration text into subtitle lines by Chinese/ASCII sentence
 * punctuation and newlines; further break lines longer than maxLen.
 * @param {string} text
 * @param {number} [maxLen=16]
 * @returns {string[]}
 */
export function splitSubtitleLines(text, maxLen = 16) {
  const normalized = String(text || '').trim();
  if (!normalized) return [];
  const tokens = [];
  let buf = '';
  for (const ch of normalized) {
    buf += ch;
    if (BREAK_SET.has(ch) || ch === '\n') {
      tokens.push(buf);
      buf = '';
    }
  }
  if (buf.trim()) tokens.push(buf);
  const sentences = tokens.map((t) => t.trim()).filter(Boolean);

  const lines = [];
  for (const sentence of sentences) {
    if (sentence.length <= maxLen + MAX_PLUS_CARRY) {
      lines.push(sentence);
      continue;
    }
    // break by characters, keep odd leftover on the previous line boundary
    let start = 0;
    while (start < sentence.length) {
      lines.push(sentence.slice(start, start + maxLen));
      start += maxLen;
    }
  }
  return lines;
}
```

- [x] **Step 4: Run test to verify it passes**

Run: `npx vitest run clio/ui/static/src/__tests__/plan-subtitle.test.js -t splitSubtitleLines`
Expected: PASS (5 tests).

- [x] **Step 5: Commit**

```bash
git add clio/ui/static/src/plan-subtitle.js clio/ui/static/src/__tests__/plan-subtitle.test.js
git commit -m "feat(ui): subtitle line splitter pure helper"
```

---

### Task 2: Pure helper `scheduleSubtitleTiming`

**Files:**
- Modify: `clio/ui/static/src/plan-subtitle.js`
- Test: `clio/ui/static/src/__tests__/plan-subtitle.test.js`

> Add to the existing test file's import list: `scheduleSubtitleTiming`.

- [x] **Step 1: Write the failing test**

```js
// append inside plan-subtitle.test.js
import { scheduleSubtitleTiming } from '../plan-subtitle.js';

describe('scheduleSubtitleTiming', () => {
  it('evenly distributes 2 lines over 60s', () => {
    const s = scheduleSubtitleTiming(60, 2);
    expect(s).toEqual([
      { startSec: 0, endSec: 30, index: 0 },
      { startSec: 30, endSec: 60, index: 1 },
    ]);
  });

  it('last line clamped to duration', () => {
    const s = scheduleSubtitleTiming(31, 2);
    expect(s[1].endSec).toBe(31);
    expect(s[1].startSec).toBeCloseTo(15.5);
  });

  it('3 lines over 30s', () => {
    const s = scheduleSubtitleTiming(30, 3);
    expect(s.map((x) => x.startSec)).toEqual([0, 10, 20]);
    expect(s[2].endSec).toBe(30);
  });

  it('lineCount 0 → []', () => {
    expect(scheduleSubtitleTiming(60, 0)).toEqual([]);
    expect(scheduleSubtitleTiming(60, -2)).toEqual([]);
  });

  it('non-finite / zero duration → []', () => {
    expect(scheduleSubtitleTiming(0, 3)).toEqual([]);
    expect(scheduleSubtitleTiming(NaN, 3)).toEqual([]);
    expect(scheduleSubtitleTiming(-5, 3)).toEqual([]);
    expect(scheduleSubtitleTiming(Number.POSITIVE_INFINITY, 3)).toEqual([]);
  });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `npx vitest run clio/ui/static/src/__tests__/plan-subtitle.test.js -t scheduleSubtitleTiming`
Expected: FAIL (function not defined).

- [x] **Step 3: Write minimal implementation**

```js
// append to plan-subtitle.js

/**
 * Evenly distribute lineCount lines across a segment duration.
 * @param {number} durationSec
 * @param {number} lineCount
 * @returns {Array<{startSec: number, endSec: number, index: number}>}
 */
export function scheduleSubtitleTiming(durationSec, lineCount) {
  const d = Number(durationSec);
  const n = Number(lineCount);
  if (!(d > 0) || !Number.isFinite(d)) return [];
  if (!(n > 0) || !Number.isFinite(n)) return [];
  const step = d / n;
  const out = [];
  for (let i = 0; i < n; i++) {
    const start = i * step;
    const end = i === n - 1 ? d : (i + 1) * step;
    out.push({ startSec: start, endSec: end, index: i });
  }
  return out;
}
```

- [x] **Step 4: Run test to verify it passes**

Run: `npx vitest run clio/ui/static/src/__tests__/plan-subtitle.test.js -t scheduleSubtitleTiming`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add clio/ui/static/src/plan-subtitle.js clio/ui/static/src/__tests__/plan-subtitle.test.js
git commit -m "feat(ui): subtitle timing scheduler pure helper"
```

---

### Task 3: Pure helper `subtitleIndexAtTime`

**Files:**
- Modify: `clio/ui/static/src/plan-subtitle.js`
- Test: `clio/ui/static/src/__tests__/plan-subtitle.test.js`

> Add `subtitleIndexAtTime` to the import list.

- [x] **Step 1: Write the failing test**

```js
// append inside plan-subtitle.test.js
import { subtitleIndexAtTime } from '../plan-subtitle.js';

describe('subtitleIndexAtTime', () => {
  const schedule = [
    { startSec: 0, endSec: 15, index: 0 },
    { startSec: 15, endSec: 30, index: 1 },
  ];

  it('boundary: startSec inclusive', () => {
    expect(subtitleIndexAtTime(schedule, 0)).toBe(0);
    expect(subtitleIndexAtTime(schedule, 15)).toBe(1);
  });

  it('endSec exclusive', () => {
    expect(subtitleIndexAtTime(schedule, 14.999)).toBe(0);
    expect(subtitleIndexAtTime(schedule, 30)).toBeNull();
  });

  it('mid range', () => {
    expect(subtitleIndexAtTime(schedule, 7)).toBe(0);
    expect(subtitleIndexAtTime(schedule, 22)).toBe(1);
  });

  it('empty schedule → null', () => {
    expect(subtitleIndexAtTime([], 5)).toBeNull();
  });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `npx vitest run clio/ui/static/src/__tests__/plan-subtitle.test.js -t subtitleIndexAtTime`
Expected: FAIL (function not defined).

- [x] **Step 3: Write minimal implementation**

```js
// append to plan-subtitle.js

/**
 * Index of the subtitle line active at localSec, or null when out of range.
 * Half-open intervals [startSec, endSec).
 * @param {{startSec:number,endSec:number,index:number}[]} schedule
 * @param {number} localSec
 * @returns {number|null}
 */
export function subtitleIndexAtTime(schedule, localSec) {
  if (!Array.isArray(schedule) || schedule.length === 0) return null;
  const t = Number(localSec);
  if (!Number.isFinite(t)) return null;
  for (const slot of schedule) {
    if (t >= slot.startSec && t < slot.endSec) return slot.index;
  }
  return null;
}
```

- [x] **Step 4: Run test to verify it passes**

Run: `npx vitest run clio/ui/static/src/__tests__/plan-subtitle.test.js -t subtitleIndexAtTime`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add clio/ui/static/src/plan-subtitle.js clio/ui/static/src/__tests__/plan-subtitle.test.js
git commit -m "feat(ui): subtitle line-at-time lookup pure helper"
```

---

### Task 4: DOM overlay element + CSS

**Files:**
- Modify: `clio/ui/static/index.html:97-99`
- Modify: `clio/ui/static/style.css:765-776`

- [x] **Step 1: Add the subtitle div in the player wrap**

In `clio/ui/static/index.html`, inside `.player-wrap`, after `<video id="player" ...></video>` (line 98), insert:

```html
<div class="player-wrap">
  <video id="player" controls preload="metadata"></video>
  <div id="plan-subtitle" class="plan-subtitle" hidden></div>
</div>
```

- [x] **Step 2: Add CSS position + subtitle style**

In `clio/ui/static/style.css`, at `.player-wrap` rule (line 765), add `position: relative;`:

```css
.player-wrap {
  background: #000; border-radius: var(--radius-md); overflow: hidden;
  display: flex; justify-content: center;
  aspect-ratio: 16 / 9;
  position: relative; /* anchor for plan-subtitle */
}
```

Then add a new rule below `#player` (after line 776):

```css
.plan-subtitle {
  position: absolute; left: 50%; transform: translateX(-50%);
  bottom: 24px; z-index: 4; max-width: 80%;
  padding: 6px 14px; border-radius: 8px;
  background: rgba(0,0,0,.55); color: #fff;
  font-size: 22px; line-height: 1.4; text-align: center;
  text-shadow: 0 1px 3px rgba(0,0,0,.8);
  pointer-events: none; overflow-wrap: break-word;
}
.plan-subtitle[hidden] { display: none; }
```

- [x] **Step 3: Verify CSS via existing tests**

Run: `npx vitest run clio/ui/static/src/__tests__/player-layout.test.js`
Expected: PASS (existing layout test still passes with the added property).

- [x] **Step 4: Commit**

```bash
git add clio/ui/static/index.html clio/ui/static/style.css
git commit -m "feat(ui): plan subtitle overlay element and styles"
```

---

### Task 5: Loader `loadVoiceoverText`

**Files:**
- Modify: `clio/ui/static/src/plan-subtitle.js`
- Test: `clio/ui/static/src/__tests__/plan-subtitle.test.js`

> Add `loadVoiceoverText` to the import list.

- [x] **Step 1: Write the failing test**

```js
// append inside plan-subtitle.test.js
import { loadVoiceoverText } from '../plan-subtitle.js';

describe('loadVoiceoverText', () => {
  const el = document.createElement('div');

  it('caches per index via script_json', async () => {
    // stub api.js fetch through global fetch by providing script_json that
    // maps to a fake endpoint; instead we call with an explicit fake loader.
    const fakeLoader = async () => ({ voiceover: '测试字幕' });
    const text = await loadVoiceoverText('001', 'a.json', fakeLoader);
    expect(text).toBe('测试字幕');
  });

  it('handles missing script_json → null', async () => {
    const fetched = [];
    const fakeLoader = async (url) => { fetched.push(url); return null; };
    const text = await loadVoiceoverText('002', null, fakeLoader);
    expect(text).toBeNull();
    expect(fetched).toEqual([]);
  });

  it('handles loader failure → null', async () => {
    const fakeLoader = async () => { throw new Error('boom'); };
    const text = await loadVoiceoverText('003', 'c.json', fakeLoader);
    expect(text).toBeNull();
  });

  it('caches resolved value: second call does not refetch', async () => {
    let calls = 0;
    const fakeLoader = async () => { calls += 1; return { voiceover: '缓存' }; };
    await loadVoiceoverText('004', 'd.json', fakeLoader);
    await loadVoiceoverText('004', 'd.json', fakeLoader);
    expect(calls).toBe(1);
  });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `npx vitest run clio/ui/static/src/__tests__/plan-subtitle.test.js -t loadVoiceoverText`
Expected: FAIL (function not defined or signature mismatch).

- [x] **Step 3: Write minimal implementation**

Signature: `loadVoiceoverText(index, scriptJson, fetchFn = apiFetch)`. Third param is the **injectable fetch** used by tests (avoids depending on network / `api()` in jsdom). When absent, default to `api()`.

Add imports at top of `plan-subtitle.js`:

```js
import { api } from './api.js';
```

Cache is a module-scoped Map.

```js
// append to plan-subtitle.js

const _voiceoverCache = new Map(); // index -> Promise<string|null>

/** Default fetcher: calls /api/voiceover via clio api(). */
async function apiFetch(scriptJson) {
  const d = await api('GET', `/api/voiceover?file=${encodeURIComponent(scriptJson)}`);
  const text = d && typeof d.voiceover === 'string' ? d.voiceover.trim() : '';
  return text || null;
}

/**
 * Get spoken narration text for a video index. Cached per index.
 * @param {string|number} index
 * @param {string|null} scriptJson  video.script_json basename
 * @param {function|null} [fetchFn] injectable fetcher (tests); default apiFetch
 * @returns {Promise<string|null>}
 */
export function loadVoiceoverText(index, scriptJson, fetchFn = apiFetch) {
  const key = String(index ?? '');
  const cached = _voiceoverCache.get(key);
  if (cached) return cached;
  if (!scriptJson) {
    const p = Promise.resolve(null);
    _voiceoverCache.set(key, p);
    return p;
  }
  const p = Promise.resolve()
    .then(() => fetchFn(scriptJson))
    .catch(() => null);
  _voiceoverCache.set(key, p);
  return p;
}
```

- [x] **Step 4: Run test to verify it passes**

Run: `npx vitest run clio/ui/static/src/__tests__/plan-subtitle.test.js -t loadVoiceoverText`
Expected: PASS (4 tests).

- [x] **Step 5: Commit**

```bash
git add clio/ui/static/src/plan-subtitle.js clio/ui/static/src/__tests__/plan-subtitle.test.js
git commit -m "feat(ui): cached voiceover subtitle loader"
```

---

### Task 6: DOM renderer `renderPlanSubtitle` + `hidePlanSubtitle`

**Files:**
- Modify: `clio/ui/static/src/plan-subtitle.js`
- Test: `clio/ui/static/src/__tests__/plan-subtitle.test.js`

> This renders into the real `#plan-subtitle` element (jsdom). Tests pass an **options object** with an injected `ctx` and `textFor` so no `state`/network is needed. Signature: `renderPlanSubtitle(opts)` where `opts = { ctx?, textFor? }`.

- [x] **Step 1: Write the failing test**

```js
// append inside plan-subtitle.test.js
import { renderPlanSubtitle, hidePlanSubtitle } from '../plan-subtitle.js';

function setPlayerSubtitleEl() {
  const el = document.createElement('div');
  el.id = 'plan-subtitle';
  el.hidden = true;
  document.body.appendChild(el);
  return el;
}

const baseCtx = {
  entity: 'plan', previewIndex: 0,
  plan: { sequence: [{ index: '001', use_timeline: '00:00-00:30' }] },
  videos: [{ index: '001', script_json: 'vy.json' }],
  previewGlobalSec: 5,
};

describe('renderPlanSubtitle / hidePlanSubtitle', () => {
  beforeEach(() => {
    document.getElementById('plan-subtitle')?.remove();
  });

  it('renders the active line into the element', async () => {
    const el = setPlayerSubtitleEl();
    await renderPlanSubtitle({ ctx: baseCtx, textFor: async () => '第一行。第二行。' });
    expect(el.hidden).toBe(false);
    expect(el.textContent).toBe('第一行。');
  });

  it('skips DOM write when line unchanged', async () => {
    const el = setPlayerSubtitleEl();
    await renderPlanSubtitle({ ctx: baseCtx, textFor: async () => '一句。' });
    const t1 = el.textContent;
    await renderPlanSubtitle(
      { ctx: { ...baseCtx, previewGlobalSec: 6 }, textFor: async () => '一句。' },
    );
    expect(el.textContent).toBe(t1);
  });

  it('re-writes when line changes', async () => {
    const el = setPlayerSubtitleEl();
    await renderPlanSubtitle({ ctx: baseCtx, textFor: async () => '第一句。第二句。' });
    const t1 = el.textContent;
    // jump to global 16s → second line (duration 30, 2 lines -> line1 at [15,30))
    await renderPlanSubtitle(
      { ctx: { ...baseCtx, previewGlobalSec: 16 }, textFor: async () => '第一句。第二句。' },
    );
    expect(el.textContent).not.toBe(t1);
    expect(el.textContent).toBe('第二句。');
  });

  it('hides when entity is not plan', async () => {
    const el = setPlayerSubtitleEl();
    await renderPlanSubtitle({ ctx: { ...baseCtx, entity: 'video' }, textFor: async () => 'x' });
    expect(el.hidden).toBe(true);
  });

  it('hides when segment missing/no script_json', async () => {
    const el = setPlayerSubtitleEl();
    await renderPlanSubtitle({ ctx: { ...baseCtx, videos: [] }, textFor: async () => 'x' });
    expect(el.hidden).toBe(true);
  });

  it('hides when text empty / null', async () => {
    const el = setPlayerSubtitleEl();
    await renderPlanSubtitle({ ctx: baseCtx, textFor: async () => null });
    expect(el.hidden).toBe(true);
  });

  it('hidePlanSubtitle sets hidden', async () => {
    const el = setPlayerSubtitleEl();
    el.hidden = false;
    hidePlanSubtitle();
    expect(el.hidden).toBe(true);
  });

  it('renders null line when localSec at/after segment end', async () => {
    const el = setPlayerSubtitleEl();
    // use_timeline 00:00-00:30, complete 30s of sequence -> previewGlobalSec 30 maps to end
    await renderPlanSubtitle(
      { ctx: { ...baseCtx, previewGlobalSec: 30 }, textFor: async () => '一句。' },
    );
    expect(el.hidden).toBe(true);
  });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `npx vitest run clio/ui/static/src/__tests__/plan-subtitle.test.js -t renderPlanSubtitle`
Expected: FAIL (functions not defined).

- [x] **Step 3: Write minimal implementation**

`renderPlanSubtitle` reads a context object (`opts.ctx`, or `readStateContext()` for production) and an injectable `opts.textFor(index, scriptJson) -> Promise<string|null>` (defaults to `loadVoiceoverText`). All production entry points use `renderPlanSubtitleFromState()`; tests pass `ctx` + a stub `textFor`.

```js
// append to plan-subtitle.js

import { state } from './state.js';
import { buildTimeline } from './plan-timeline.js';

/**
 * Build a context object from the current app state for renderPlanSubtitle.
 * Pure and cheap -> callable from every timeupdate.
 */
function readStateContext() {
  return {
    entity: state.currentEntity,
    previewIndex: state.previewIndex,
    plan: state.plan,
    videos: state.videos,
    previewGlobalSec: state.previewGlobalSec,
  };
}

/** @returns {HTMLElement|null} */
function subtitleElement() {
  return document.getElementById('plan-subtitle');
}

/**
 * Render the active subtitle line into #plan-subtitle; hide when nothing
 * should show. opts.ctx overrides reading app state (tests). opts.textFor
 * resolves the narration text for (index, scriptJson); default loadVoiceoverText.
 *
 * @param {{ctx?: object, textFor?: function}} [opts]
 * @returns {Promise<void>}
 */
export async function renderPlanSubtitle(opts = {}) {
  const el = subtitleElement();
  if (!el) return;
  const c = opts.ctx || readStateContext();
  const textFor = opts.textFor || loadVoiceoverText;
  const clear = () => { el.hidden = true; el.dataset.line = ''; };

  if (c.entity !== 'plan' || !Number.isFinite(c.previewIndex) || c.previewIndex < 0) {
    clear(); return;
  }
  const p = c.plan;
  const seg = p?.sequence?.[c.previewIndex];
  if (!seg) { clear(); return; }

  const idx = String(seg.index ?? '');
  const v = (c.videos || []).find((x) => String(x.index) === idx);
  if (!v || !v.script_json) { clear(); return; }

  const text = await textFor(idx, v.script_json);
  // Stale-guard: user may have sought to another segment while awaiting.
  const live = opts.ctx ? opts.ctx : readStateContext();
  const current = live.entity === 'plan'
    && live.previewIndex === c.previewIndex
    && String(live.plan?.sequence?.[live.previewIndex]?.index ?? '') === idx;
  if (!current || !text) { clear(); return; }

  const lines = splitSubtitleLines(text);
  if (!lines.length) { clear(); return; }

  const tl = buildTimeline((p?.sequence) || []);
  const tseg = tl.segments[c.previewIndex];
  if (!tseg || tseg.duration <= 0) { clear(); return; }

  const schedule = scheduleSubtitleTiming(tseg.duration, lines.length);
  const localSec = Math.min(tseg.duration, Math.max(0, c.previewGlobalSec - tseg.globalStart));
  const lineIdx = subtitleIndexAtTime(schedule, localSec);
  if (lineIdx == null) { clear(); return; }

  const content = lines[lineIdx];
  if (el.dataset.line === String(lineIdx) && !el.hidden && el.textContent === content) {
    return; // no change
  }
  el.textContent = content;
  el.dataset.line = String(lineIdx);
  el.hidden = false;
}

/** Hide the subtitle layer (e.g. leaving plan mode / stopping preview). */
export function hidePlanSubtitle() {
  const el = subtitleElement();
  if (el) { el.hidden = true; el.dataset.line = ''; }
}

/** Production entry point: render from current app state. */
export function renderPlanSubtitleFromState() {
  return renderPlanSubtitle({ textFor: loadVoiceoverText });
}
```

- [x] **Step 4: Run test to verify it passes**

Run: `npx vitest run clio/ui/static/src/__tests__/plan-subtitle.test.js -t renderPlanSubtitle`
Expected: PASS (8 tests).

- [x] **Step 5: Commit**

```bash
git add clio/ui/static/src/plan-subtitle.js clio/ui/static/src/__tests__/plan-subtitle.test.js
git commit -m "feat(ui): plan subtitle DOM renderer"
```

---

### Task 7: Wire into `viewer.js`

**Files:**
- Modify: `clio/ui/static/src/viewer.js` (imports line 1-22; `seekToGlobal` ~line 201; `ontimeupdate` ~line 660-677; `stopPreview` ~line 542; `renderPreviewBar` non-plan branch ~line 356-360)

- [x] **Step 1: Import the renderers**

Near the top import block (after line 22), add:

```js
import { renderPlanSubtitleFromState, hidePlanSubtitle } from './plan-subtitle.js';
```

- [x] **Step 2: Render after seeking to a segment**

In `seekToGlobal`, after `_loadAndSeekSource(v, seekSec, wantPlay);` (around line 201), add:

```js
  renderPlanSubtitleFromState();
```

- [x] **Step 3: Render on timeupdate**

In `player.ontimeupdate`, inside the `isGlobalTimelineUi() && state.previewIndex >= 0` block, after `updateCompositeClock();` (near line 677), add:

```js
      renderPlanSubtitleFromState();
```

- [x] **Step 4: Hide on stopPreview**

In `stopPreview` (after `player.pause();`, near line 547), add:

```js
  hidePlanSubtitle();
```

- [x] **Step 5: Hide when leaving plan mode**

In `renderPreviewBar`, the non-plan branch `if (!isPlan) { ... return; }` (line 356), add `hidePlanSubtitle();` **before** the `return;`:

```js
  if (!isPlan) {
    hidePlanSubtitle();
    if (_wasPlanBar) {
      ...
    }
    return;
  }
```

- [x] **Step 6: Verify no syntax errors**

Run: `node --check clio/ui/static/src/viewer.js`
Expected: no output (valid).

- [x] **Step 7: Run frontend suite**

Run: `npx vitest run`
Expected: all existing frontend tests pass (incl. `viewer`/`preview-bar` consumer tests unaffected).

- [x] **Step 8: Commit**

```bash
git add clio/ui/static/src/viewer.js
git commit -m "feat(ui): drive plan subtitles from preview playback"
```

---

### Task 8: Full-suite verification + ruff

**Files:** none (verification only).

- [x] **Step 1: Run entire frontend suite**

Run: `npm test`
Expected: all Vitest suites pass, including new `plan-subtitle.test.js`.

- [x] **Step 2: Run Python tests (confirm no backend change regressions)**

Run: `python -m pytest clio/tests/ -q`
Expected: pass (no Python code touched by this feature; should be unchanged from baseline).

- [x] **Step 3: Update AGENTS.md / CHANGELOG if appropriate**

No AGENTS.md change needed (no new conventions). Add a CHANGELOG entry for the feature if the project tracks per-feature changelog entries (see `CHANGELOG.md` format used by prior 2026-08-05 entry).

- [x] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog plan-preview floating subtitles"
```

---

## Self-Review

**Spec coverage:**
- Goals 1-5 mirrored in Tasks 1-3 (pure helpers) + 4-6 (DOM/loader/renderer) + 7 (viewer wiring).
- Success criteria: line advance (Task 7 timeupdate), hidden on leave-plan (Task 7 renderPreviewBar), hidden on no `script_json`/empty text/duration 0 (Task 6 renderer guards), fetch failure (Task 5 loader returns null → Task 6 hides), localSec out of range (Task 6 + `subtitleIndexAtTime` null).
- Non-goals respected: no backend/plan-file/prompt changes anywhere.

**Placeholder scan:** every code step contains full, runnable code and exact commands. No "TBD/TODO" or vague instructions.

**Type consistency:** `splitSubtitleLines(text, maxLen)`, `scheduleSubtitleTiming(durationSec, lineCount)`, `subtitleIndexAtTime(schedule, localSec)`, `loadVoiceoverText(index, scriptJson, fetchFn?)`, `renderPlanSubtitle(opts)` (opts = `{ctx?, textFor?}`), `hidePlanSubtitle()`, `renderPlanSubtitleFromState()` — names and signatures consistent across Tasks 1-7. Each function defined once, used in later tasks with matching signature (e.g. Task 7 imports `renderPlanSubtitleFromState`/`hidePlanSubtitle`; Task 5 passes `fetchFn` as 3rd arg to `loadVoiceoverText`; Task 6 tests call `renderPlanSubtitle({ctx, textFor})`).