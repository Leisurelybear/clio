// Pure helpers for plan-preview floating subtitles. No DOM.

import { api } from './api.js';
import { state } from './state.js';
import { buildTimeline } from './plan-timeline.js';

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
    let start = 0;
    while (start < sentence.length) {
      lines.push(sentence.slice(start, start + maxLen));
      start += maxLen;
    }
  }
  return lines;
}

/**
 * Group text into sentences, each expanded into its wrapped lines.
 * @param {string} text
 * @param {number} maxLen
 * @returns {string[][]} one entry per sentence (its wrapped lines)
 */
function groupWrappedSentences(text, maxLen) {
  const normalized = String(text || '').trim();
  if (!normalized) return [];
  const sentences = [];
  let buf = '';
  for (const ch of normalized) {
    buf += ch;
    if (BREAK_SET.has(ch) || ch === '\n') {
      const t = buf.trim();
      if (t) sentences.push(t);
      buf = '';
    }
  }
  const t = buf.trim();
  if (t) sentences.push(t);

  const groups = [];
  for (const sentence of sentences) {
    if (sentence.length <= maxLen + MAX_PLUS_CARRY) {
      groups.push([sentence]);
      continue;
    }
    const lines = [];
    let start = 0;
    while (start < sentence.length) {
      lines.push(sentence.slice(start, start + maxLen));
      start += maxLen;
    }
    groups.push(lines);
  }
  return groups;
}

/**
 * Segment narration into display batches based on mode.
 *  auto => each sentence stays intact; batches hold up to maxLines lines,
 *          breaking before a sentence only when it would overflow the limit.
 *  multi=> flat wrapped lines chunked strictly at maxLines (can split a long
 *          sentence across batches).
 *  scroll=> single batch with the joined full text on one (long) line.
 * @param {string} text
 * @param {{mode?:string, maxLines?:number, maxLen?:number}} [opts]
 * @returns {string[][]} batches of line strings
 */
export function planSubtitleBatches(text, opts = {}) {
  const mode = opts.mode || 'auto';
  const maxLines = Math.max(1, opts.maxLines || 2);
  const maxLen = Math.max(1, opts.maxLen || 16);
  const groups = groupWrappedSentences(text, maxLen);
  if (!groups.length) return [];

  if (mode === 'multi') {
    const flat = groups.flat();
    const batches = [];
    for (let i = 0; i < flat.length; i += maxLines) {
      batches.push(flat.slice(i, i + maxLines));
    }
    return batches;
  }
  if (mode === 'scroll') {
    return [[groups.flat().join('')]];
  }
  // auto
  const batches = [];
  let cur = [];
  let curLines = 0;
  for (const lines of groups) {
    if (curLines > 0 && curLines + lines.length > maxLines) {
      batches.push(cur);
      cur = [];
      curLines = 0;
    }
    cur.push(...lines);
    curLines += lines.length;
  }
  if (cur.length) batches.push(cur);
  return batches;
}

/**
 * Evenly distribute batchCount batches across durationSec.
 * @param {number} durationSec
 * @param {number} batchCount
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

/**
 * Active batch index at localSec, or null when out of range (half-open [start,end)).
 * @param {Array<{startSec:number,endSec:number,index:number}>} schedule
 * @param {number} localSec
 * @returns {number|null}
 */
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
 * Effective font px so text fits a container. Linear scale down toward
 * minFontSize as chars exceed containerMaxChars.
 * @param {string} text
 * @param {number} basePx
 * @param {number} containerMaxChars
 * @param {number} minFontSize
 * @returns {number} effective px (>= minFontSize, <= basePx)
 */
export function computeFontShrink(text, basePx, containerMaxChars, minFontSize) {
  const len = String(text || '').length;
  if (len <= 0) return basePx;
  if (len <= containerMaxChars) return basePx;
  const ratio = containerMaxChars / len;
  const eff = basePx * ratio;
  return Math.max(minFontSize, Math.min(basePx, Math.round(eff * 10) / 10));
}

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

const _voiceoverCache = new Map(); // index -> Promise<string|null>

/** Default fetcher: loads the voiceover file via clio api(). */
async function apiFetch(scriptJson) {
  return api('GET', `/api/voiceover?file=${encodeURIComponent(scriptJson)}`);
}

/**
 * Get spoken narration text for a video index. Cached per index.
 * fetchFn returns the raw voiceover JSON (e.g. {voiceover, ...}); the
 * `voiceover` string field is extracted and trimmed here.
 * @param {string|number} index
 * @param {string|null} scriptJson  video.script_json basename
 * @param {function|null} [fetchFn] injectable fetcher (tests); default apiFetch
 * @returns {Promise<string|null>}
 */
export function loadVoiceoverText(index, scriptJson, fetchFn = apiFetch) {
  const key = `${String(index ?? '')}:${scriptJson ?? ''}`;
  const cached = _voiceoverCache.get(key);
  if (cached) return cached;
  if (!scriptJson) {
    const p = Promise.resolve(null);
    _voiceoverCache.set(key, p);
    return p;
  }
  const p = Promise.resolve()
    .then(() => fetchFn(scriptJson))
    .then((d) => {
      const text = d && typeof d.voiceover === 'string' ? d.voiceover.trim() : '';
      return text || null;
    })
    .catch(() => null);
  _voiceoverCache.set(key, p);
  return p;
}

/**
 * Drop cached voiceover text. With an index arg only that entry is cleared;
 * without args the whole cache is emptied (e.g. after batch edits).
 * @param {string|number} [index]
 */
export function invalidateVoiceoverCache(index) {
  if (index == null) { _voiceoverCache.clear(); return; }
  _voiceoverCache.delete(String(index));
}

/**
 * Build a context object from the current app state for renderPlanSubtitle.
 * Pure and cheap -> callable from every timeupdate.
 */
function readStateContext() {
  const project = state.configProject || {};
  return {
    entity: state.currentEntity,
    previewIndex: state.previewIndex,
    plan: state.plan,
    videos: state.videos,
    previewGlobalSec: state.previewGlobalSec,
    config: project,
  };
}

/** @returns {HTMLElement|null} */
function subtitleElement() {
  return document.getElementById('plan-subtitle');
}

/**
 * Resolve effective subtitle settings, filling config-driven values with
 * sensible defaults.
 * @param {{preview?:{subscribeconnections?:object}} | null} [config]
 * @returns {{enabled:boolean, mode:string, maxLines:number, maxLen:number,
 *             fontPx:number, shrinkMinPx:number}}
 */
export function subtitleSettings(config) {
  const s = config?.preview?.subtitles || {};
  return {
    enabled: s.enabled !== false,
    mode: s.mode || 'auto',
    maxLines: Math.max(1, Number(s.max_lines) || 2),
    maxLen: Math.max(1, Number(s.max_len_per_line) || 16),
    fontPx: Number(s.font_size) || 22,
    fontSizeMinPx: Number(s.min_font_size) || 14,
    scrollSpeed: Math.max(1, Number(s.scroll_speed) || 40),
  };
}

/**
 * Map resolved settings + position to CSS variables on the subtitle element.
 * Pure except for writing to the passed element.
 * @param {HTMLElement} el
 * @param {object} s resolved subtitleSettings() output
 * @param {{color?:string, background?:string, outline_color?:string,
 *           font_family?:string, pos_x?:number, pos_y?:number}} [cfg]
 */
export function applySubtitleStyle(el, s, cfg = {}) {
  const elCfg = cfg;
  el.style.setProperty('--st-font-size', `${s.fontPx}px`);
  el.style.setProperty('--st-color', vCfg(elCfg.font_color) || '#fff');
  el.style.setProperty('--st-bg', vCfg(elCfg.background) || 'rgba(0,0,0,.55)');
  el.style.setProperty(
    '--st-outline',
    vCfg(elCfg.outline) || '0 0 2px rgba(0,0,0,.8)',
  );
  // font_family="" means follow system; otherwise emit the family unquoted so
  // a comma-separated stack (e.g. "system-ui, sans-serif") is treated correctly.
  const family = vCfg(elCfg.font_family);
  el.style.setProperty('--st-font-family', family ? family.replace(/^['"]|['"]$/g, '') : 'system-ui, sans-serif');
  const [posX, posY] = clampPositionPct(Number(elCfg.pos_x), Number(elCfg.pos_y));
  el.style.setProperty('--st-pos-x', pxToPct(posX));
  el.style.setProperty('--st-pos-y', pxToPct(posY));
  el.dataset.posX = String(posX);
  el.dataset.posY = String(posY);
}

function vCfg(v) { return v == null || v === '' ? null : v; }
function pxToPct(n) { return `${n}%`; }
function effectiveFontPx(n) { return Number.isFinite(n) && n > 0 ? n : 22; }

export function resolveSegmentSubtitleText(seg, voiceoverText) {
  const planText = typeof seg?.subtitle === 'string' ? seg.subtitle.trim() : '';
  if (planText) return planText;
  return voiceoverText;
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

  const st = subtitleSettings(c.config);
  const cfg = c.config?.preview?.subtitles || {};

  if (c.entity !== 'plan' || !Number.isFinite(c.previewIndex) || c.previewIndex < 0) {
    clear(); return;
  }
  if (!st.enabled) { clear(); return; }
  const p = c.plan;
  const seg = p?.sequence?.[c.previewIndex];
  if (!seg) { clear(); return; }

  const idx = String(seg.index ?? '');
  const v = (c.videos || []).find((x) => String(x.index) === idx);
  const segHasSubtitle = typeof seg.subtitle === 'string' && seg.subtitle.trim() !== '';
  if (!segHasSubtitle && (!v || !v.script_json)) { clear(); return; }

  const voiceText = segHasSubtitle ? null : (v?.script_json ? await textFor(idx, v.script_json) : null);
  const text = resolveSegmentSubtitleText(seg, voiceText);
  // Stale-guard: user may have sought to another segment while awaiting.
  const live = opts.ctx ? opts.ctx : readStateContext();
  const current = live.entity === 'plan'
    && live.previewIndex === c.previewIndex
    && String(live.plan?.sequence?.[live.previewIndex]?.index ?? '') === idx;
  if (!current || !text) { clear(); return; }

  const batches = planSubtitleBatches(text, {
    mode: st.mode, maxLines: st.maxLines, maxLen: st.maxLen,
  });
  if (!batches.length) { clear(); return; }

  const tl = buildTimeline((p?.sequence) || []);
  const tseg = tl.segments[c.previewIndex];
  if (!tseg || tseg.duration <= 0) { clear(); return; }

  const schedule = scheduleBatchTiming(tseg.duration, batches.length);
  const localSec = Math.min(tseg.duration, Math.max(0, c.previewGlobalSec - tseg.globalStart));
  const batchIdx = packAtTime(schedule, localSec);
  if (batchIdx == null) { clear(); return; }

  const lines = batches[batchIdx];
  const textEl = el.querySelector('.plan-subtitle-text');
  const content = lines.filter(Boolean).join('\n');

  applySubtitleStyle(el, st, cfg);

  // Shrink font to fit very long lines (scroll mode especially).
  const effectiveFont = st.mode === 'scroll'
    ? computeFontShrink(content, st.fontPx, st.maxLen * st.maxLines + 12, st.fontSizeMinPx)
    : st.fontPx;
  el.style.setProperty('--st-font-size', `${effectiveFontPx(effectiveFont)}px`);

  // Mode flag + scroll animation vars. The marquee translates the text track
  // by its own width, so its --st-scroll-duration paces scroll speed roughly
  // proportional to the text length.
  el.dataset.mode = st.mode;
  if (st.mode === 'scroll') {
    el.style.setProperty('--st-scroll-speed', `${st.scrollSpeed}px/s`);
    const scrollChars = content.length * effectiveFontPx(effectiveFont) * 0.6;
    const duration = Math.max(4, Math.round((scrollChars / st.scrollSpeed) * 10) / 10);
    el.style.setProperty('--st-scroll-duration', `${duration}s`);
  } else {
    el.style.setProperty('--st-scroll-speed', '');
    el.style.setProperty('--st-scroll-duration', '');
  }

  const key = String(batchIdx);
  if (el.dataset.line === key && !el.hidden && (textEl?.textContent || '') === content) {
    return; // no change
  }
  if (textEl) textEl.textContent = content;
  el.dataset.line = key;
  el.hidden = false;
}

/** Hide the subtitle layer (e.g. leaving plan mode / stopping preview). */
export function hidePlanSubtitle() {
  const el = subtitleElement();
  if (el) { el.hidden = true; el.dataset.line = ''; }
}

const DEFAULT_POS_X = 50;
const DEFAULT_POS_Y = 8;

/**
 * Clamp x/y percentages into [0,100]. Non-finite values fall back to the
 * defaults so a missing config still lands the subtitle on-screen.
 * @param {number} x
 * @param {number} y
 * @returns {[number, number]}
 */
export function clampPositionPct(x, y) {
  const cx = Number.isFinite(Number(x)) ? Math.min(100, Math.max(0, Number(x))) : DEFAULT_POS_X;
  const cy = Number.isFinite(Number(y)) ? Math.min(100, Math.max(0, Number(y))) : DEFAULT_POS_Y;
  return [cx, cy];
}

/**
 * Enable dragging the subtitle via its persistent handle. While dragging,
 * updates the element's --st-pos-* CSS vars live and reports the final
 * percentage position via onCommit (used to persist to config).
 *
 * @param {{handle:HTMLElement, stage:HTMLElement, onCommit?:function}} opts
 */
export function initSubtitleDrag({ handle, stage, onCommit }) {
  const el = subtitleElement();
  if (!el || !handle || !stage) return;
  let dragging = false;

  const move = (me) => {
    if (!dragging) return;
    const rect = stage.getBoundingClientRect();
    if (!rect || rect.width === 0 || rect.height === 0) return;
    const pctX = ((me.clientX - rect.left) / rect.width) * 100;
    // pos_y is BOTTOM-offset (0=bottom, 100=top), so invert top-relative coords.
    const pctY = ((rect.bottom - me.clientY) / rect.height) * 100;
    const [cx, cy] = clampPositionPct(pctX, pctY);
    el.style.setProperty('--st-pos-x', `${cx}%`);
    el.style.setProperty('--st-pos-y', `${cy}%`);
    el.dataset.posX = String(cx);
    el.dataset.posY = String(cy);
  };

  const endDrag = (commit) => {
    if (!dragging) return;
    dragging = false;
    handle.style.cursor = '';
    document.removeEventListener('pointermove', move);
    document.removeEventListener('pointerup', up);
    document.removeEventListener('pointercancel', cancel);
    if (!commit) return;
    // Guard against release with no prior move (NaN / stale values).
    const cx = Number(el.dataset.posX);
    const cy = Number(el.dataset.posY);
    const sx = Number.isFinite(cx) ? cx : DEFAULT_POS_X;
    const sy = Number.isFinite(cy) ? cy : DEFAULT_POS_Y;
    el.style.setProperty('--st-pos-x', `${sx}%`);
    el.style.setProperty('--st-pos-y', `${sy}%`);
    if (typeof onCommit === 'function') onCommit({ x: sx, y: sy });
  };
  const up = () => { endDrag(true); };
  const cancel = () => { endDrag(false); };

  handle.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    dragging = true;
    handle.style.cursor = 'grabbing';
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
    document.addEventListener('pointercancel', cancel);
  });
}

/** Production entry point: render from current app state. */
export function renderPlanSubtitleFromState() {
  return renderPlanSubtitle({ textFor: loadVoiceoverText });
}