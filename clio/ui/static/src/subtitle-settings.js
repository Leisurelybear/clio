export function subtitleControlsModel(config) {
  const s = config?.preview?.subtitles || {};
  const num = (v, d) => {
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? n : d;
  };
  return {
    font_size: num(s.font_size, 22),
    min_font_size: num(s.min_font_size, 14),
    font_color: safeStr(s.font_color, '#fff'),
    background: safeStr(s.background, 'rgba(0,0,0,.55)'),
    outline: safeStr(s.outline, '0 0 2px rgba(0,0,0,.8)'),
    font_family: safeStr(s.font_family, ''),
    mode: s.mode || 'auto',
    max_lines: Math.max(1, num(s.max_lines, 2)),
    max_len_per_line: Math.max(1, num(s.max_len_per_line, 16)),
  };
}

export function safeStr(v, d) {
  return v == null || v === '' ? d : String(v);
}

export function mergeSubtitleSettings(project, updates) {
  const out = project && typeof project === 'object' ? { ...project } : {};
  const preview = out.preview && typeof out.preview === 'object' ? { ...out.preview } : {};
  const subtitles = { ...(preview.subtitles && typeof preview.subtitles === 'object' ? preview.subtitles : {}) };
  for (const [k, v] of Object.entries(updates)) {
    if (v !== undefined && v !== '') subtitles[k] = v;
    else delete subtitles[k];
  }
  preview.subtitles = subtitles;
  out.preview = preview;
  return out;
}

export function safeColor(v) {
  return v == null ? null : String(v);
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
  let queued = null; // { payload, waiters: [resolve] }
  let running = false;

  const drain = () => {
    if (running || !queued) return;
    running = true;
    const entry = queued;
    queued = null;
    const run = Promise.resolve()
      .then(() => onWrite(entry.payload))
      .then(
        () => entry.waiters.forEach((r) => r()),
        () => entry.waiters.forEach((r) => r()),
      );
    tail = tail
      .then(() => run)
      .finally(() => {
        running = false;
        if (queued) drain();
      });
  };

  return (payload) => new Promise((resolve) => {
    if (queued) {
      // Coalesce a burst: supersede the pending payload, keep every waiter.
      queued.payload = payload;
      queued.waiters.push(resolve);
    } else {
      queued = { payload, waiters: [resolve] };
    }
    drain();
  });
}

const NUMERIC_KEYS = ['font_size', 'min_font_size', 'max_lines', 'max_len_per_line'];
const STRING_KEYS = ['font_color', 'background', 'outline', 'font_family', 'mode'];

export function renderSubtitleSettingsPanel(container, opts = {}) {
  const config = opts.config || null;
  const m = subtitleControlsModel(config);
  container.innerHTML = `
    <details class="subtitle-settings" open>
      <summary>字幕样式</summary>
      <div class="subtitle-settings-grid">
        <label>字号 <input type="number" min="8" max="72" data-subtle="font_size" value="${m.font_size}"></label>
        <label>最小字号 <input type="number" min="6" max="72" data-subtle="min_font_size" value="${m.min_font_size}"></label>
        <label>文字颜色 <input type="color" data-subtle="font_color" value="${m.font_color}"></label>
        <label>背景色 <input type="text" data-subtle="background" value="${m.background}" placeholder="rgba(0,0,0,.55)"></label>
        <label>描边 <input type="text" data-subtle="outline" value="${m.outline}" placeholder="0 0 2px rgba(0,0,0,.8)"></label>
        <label>字体 <input type="text" data-subtle="font_family" value="${m.font_family}" placeholder="system-ui, sans-serif"></label>
        <label>模式
          <select data-subtle="mode">
            <option value="auto" ${m.mode === 'auto' ? 'selected' : ''}>自动</option>
            <option value="multi" ${m.mode === 'multi' ? 'selected' : ''}>多行</option>
            <option value="scroll" ${m.mode === 'scroll' ? 'selected' : ''}>滚动</option>
          </select>
        </label>
        <label>最多行数 <input type="number" min="1" max="6" data-subtle="max_lines" value="${m.max_lines}"></label>
        <label>每行字数 <input type="number" min="1" max="60" data-subtle="max_len_per_line" value="${m.max_len_per_line}"></label>
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
      updates[key] = el.value;
    }
    onChange(updates);
  };
  container.querySelectorAll('[data-subtle]').forEach((el) => {
    el.addEventListener('change', emit);
    el.addEventListener('input', emit);
  });
  return container;
}