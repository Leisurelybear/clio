import { t } from './i18n.js';

const SUBTITLE_MODES = new Set(['auto', 'multi', 'scroll']);
const COLOR_RE = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$|^rgba?\(\s*[\d.%\s,/]+\s*\)$/;
const CSS_SHADOW_RE = /^[0-9a-zA-Z#%.,()\s/-]+$/;
const FONT_FAMILY_RE = /^[0-9a-zA-Z\s,"'_-]+$/;

export function safeStr(v, d) {
  return v == null || v === '' ? d : String(v);
}

/** Strict color for CSS (hex / rgb / rgba only). */
export function safeColor(v, fallback = '#ffffff') {
  if (v == null || v === '') return null;
  const s = String(v).trim();
  if (COLOR_RE.test(s)) return s;
  return fallback;
}

export function safeCssShadow(v, fallback) {
  const s = String(v ?? '').trim();
  if (!s) return fallback;
  if (s.length > 120 || !CSS_SHADOW_RE.test(s)) return fallback;
  if (/expression|url\s*\(|javascript:/i.test(s)) return fallback;
  return s;
}

export function safeFontFamily(v, fallback = '') {
  const s = String(v ?? '').trim();
  if (!s) return fallback;
  if (s.length > 80 || !FONT_FAMILY_RE.test(s)) return fallback;
  return s;
}

export function safeSubtitleMode(v, fallback = 'auto') {
  const s = String(v ?? '').trim().toLowerCase();
  return SUBTITLE_MODES.has(s) ? s : fallback;
}

export function subtitleControlsModel(config) {
  const s = config?.preview?.subtitles || {};
  const num = (v, d) => {
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? n : d;
  };
  const attr = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
  return {
    font_size: num(s.font_size, 22),
    min_font_size: num(s.min_font_size, 14),
    font_color: attr(safeColor(s.font_color, '#fff') || '#fff'),
    background: attr(safeCssShadow(s.background, 'rgba(0,0,0,.55)')),
    outline: attr(safeCssShadow(s.outline, '0 0 2px rgba(0,0,0,.8)')),
    font_family: attr(safeFontFamily(s.font_family, '')),
    mode: safeSubtitleMode(s.mode, 'auto'),
    max_lines: Math.max(1, num(s.max_lines, 2)),
    max_len_per_line: Math.max(1, num(s.max_len_per_line, 16)),
    scroll_speed: Math.max(1, num(s.scroll_speed, 40)),
  };
}

export function mergeSubtitleSettings(project, updates) {
  const out = project && typeof project === 'object' ? { ...project } : {};
  const preview = out.preview && typeof out.preview === 'object' ? { ...out.preview } : {};
  const subtitles = { ...(preview.subtitles && typeof preview.subtitles === 'object' ? preview.subtitles : {}) };
  for (const [k, v] of Object.entries(updates)) {
    if (v === undefined || v === '') {
      delete subtitles[k];
      continue;
    }
    if (k === 'mode') subtitles[k] = safeSubtitleMode(v, 'auto');
    else if (k === 'font_color') subtitles[k] = safeColor(v, '#ffffff') || '#ffffff';
    else if (k === 'background') subtitles[k] = safeCssShadow(v, 'rgba(0,0,0,.55)');
    else if (k === 'outline') subtitles[k] = safeCssShadow(v, '0 0 2px rgba(0,0,0,.8)');
    else if (k === 'font_family') subtitles[k] = safeFontFamily(v, '');
    else subtitles[k] = v;
  }
  preview.subtitles = subtitles;
  out.preview = preview;
  return out;
}

/**
 * Serialize async writes so the newest payload always wins. Older concurrent
 * writes still run to completion in order, but a stale payload can never land
 * after a newer one (avoids out-of-order overwrite of project config).
 * @param {(payload: object) => Promise|any} onWrite
 * @returns {(payload: object) => Promise}
 */
/**
 * Serialize async writes so the newest payload always wins. Older concurrent
 * writes still run to completion in order, but a stale payload can never land
 * after a newer one (avoids out-of-order overwrite of project config), and a
 * burst of rapid calls coalesces to the latest pending payload.
 * @param {(payload: object) => Promise|any} onWrite
 * @returns {(payload: object) => Promise}
 */
export function serializeLatestWrites(onWrite) {
  let tail = Promise.resolve();
  let queued = null; // { payload, waiters: [{ resolve, reject }] }
  let running = false;

  const drain = () => {
    if (running || !queued) return;
    running = true;
    const entry = queued;
    queued = null;
    const run = Promise.resolve()
      .then(() => onWrite(entry.payload))
      .then(
        () => entry.waiters.forEach(({ resolve }) => resolve()),
        (error) => entry.waiters.forEach(({ reject }) => reject(error)),
      );
    tail = tail
      .then(() => run)
      .finally(() => {
        running = false;
        if (queued) drain();
      });
  };

  return (payload) => new Promise((resolve, reject) => {
    if (queued) {
      // Coalesce a burst: supersede the pending payload, keep every waiter.
      queued.payload = payload;
      queued.waiters.push({ resolve, reject });
    } else {
      queued = { payload, waiters: [{ resolve, reject }] };
    }
    drain();
  });
}

const NUMERIC_KEYS = ['font_size', 'min_font_size', 'max_lines', 'max_len_per_line', 'scroll_speed'];
const STRING_KEYS = ['font_color', 'background', 'outline', 'font_family', 'mode'];

export function renderSubtitleSettingsPanel(container, opts = {}) {
  const config = opts.config || null;
  const m = subtitleControlsModel(config);
  container.innerHTML = `
    <details class="subtitle-settings" open>
      <summary>${t('subtitle.section')}</summary>
      <div class="subtitle-settings-grid">
        <label>${t('subtitle.font_size')} <input type="number" min="8" max="72" data-subtle="font_size" value="${m.font_size}"></label>
        <label>${t('subtitle.min_font_size')} <input type="number" min="6" max="72" data-subtle="min_font_size" value="${m.min_font_size}"></label>
        <label>${t('subtitle.font_color')} <input type="color" data-subtle="font_color" value="${m.font_color}"></label>
        <label>${t('subtitle.background')} <input type="text" data-subtle="background" value="${m.background}" placeholder="rgba(0,0,0,.55)"></label>
        <label>${t('subtitle.outline')} <input type="text" data-subtle="outline" value="${m.outline}" placeholder="0 0 2px rgba(0,0,0,.8)"></label>
        <label>${t('subtitle.font_family')} <input type="text" data-subtle="font_family" value="${m.font_family}" placeholder="system-ui, sans-serif"></label>
        <label>${t('subtitle.mode')}
          <select data-subtle="mode">
            <option value="auto" ${m.mode === 'auto' ? 'selected' : ''}>${t('subtitle.mode_auto')}</option>
            <option value="multi" ${m.mode === 'multi' ? 'selected' : ''}>${t('subtitle.mode_multi')}</option>
            <option value="scroll" ${m.mode === 'scroll' ? 'selected' : ''}>${t('subtitle.mode_scroll')}</option>
          </select>
        </label>
        <label>${t('subtitle.max_lines')} <input type="number" min="1" max="6" data-subtle="max_lines" value="${m.max_lines}"></label>
        <label>${t('subtitle.max_len_per_line')} <input type="number" min="1" max="60" data-subtle="max_len_per_line" value="${m.max_len_per_line}"></label>
        <label>${t('subtitle.scroll_speed')} <input type="number" min="1" max="300" data-subtle="scroll_speed" value="${m.scroll_speed}"></label>
      </div>
    </details>
  `;
  const onChange = opts.onChange || (() => {});
  const emit = () => {
    const updates = {};
    for (const key of NUMERIC_KEYS) {
      const el = container.querySelector(`[data-subtle="${key}"]`);
      if (!el) continue;
      const n = Number(el.value);
      if (Number.isFinite(n) && n > 0) updates[key] = n;
    }
    for (const key of STRING_KEYS) {
      const el = container.querySelector(`[data-subtle="${key}"]`);
      if (!el) continue;
      if (key === 'mode') updates[key] = safeSubtitleMode(el.value, 'auto');
      else if (key === 'font_color') updates[key] = safeColor(el.value, '#ffffff') || '#ffffff';
      else if (key === 'background') updates[key] = safeCssShadow(el.value, 'rgba(0,0,0,.55)');
      else if (key === 'outline') updates[key] = safeCssShadow(el.value, '0 0 2px rgba(0,0,0,.8)');
      else if (key === 'font_family') updates[key] = safeFontFamily(el.value, '');
      else updates[key] = el.value;
    }
    onChange(updates);
  };
  container.querySelectorAll('[data-subtle]').forEach((el) => {
    el.addEventListener('change', emit);
    el.addEventListener('input', emit);
  });
  return container;
}
