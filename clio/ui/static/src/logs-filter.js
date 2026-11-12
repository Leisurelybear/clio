// @ts-check
/** Pure helpers: session log level inference + client-side filter (R-027 / R-027d). */

/** @typedef {'debug'|'info'|'warn'|'error'} LogLevel */
/** @typedef {{ ts: number|null, text: string }} LogEntry */

const LEVEL_ORDER = { debug: 0, info: 1, warn: 2, error: 3 };

/**
 * @param {string} line
 * @returns {LogLevel}
 */
export function inferLogLevel(line) {
  const s = String(line ?? '');
  if (!s) return 'info';

  // Explicit python logging / structured tags (no leading \b — tag often starts the line)
  if (/\[(ERROR|CRITICAL|FATAL)\]/i.test(s)) return 'error';
  if (/\[WARN(?:ING)?\]/i.test(s)) return 'warn';
  if (/\[DEBUG\]/i.test(s)) return 'debug';
  if (/\[INFO\]/i.test(s)) return 'info';

  // Error heuristics (pipeline / UI Chinese)
  if (
    /Traceback \(most recent call last\)/i.test(s)
    || /\bException\b/.test(s)
    || /\bERROR\b/.test(s)
    || /✗|失败|错误|出错|pipeline failed|HTTP \d{3}/i.test(s)
  ) {
    return 'error';
  }

  // Warning heuristics
  if (
    /⚠|警告|\bWARN(?:ING)?\b/i.test(s)
    || /\[跳过|跳过\]|\[跳过/.test(s)
    || /\bskip(?:ped|ping)?\b/i.test(s)
  ) {
    return 'warn';
  }

  return 'info';
}

/**
 * @param {unknown} item
 * @returns {LogEntry}
 */
export function normalizeLogEntry(item) {
  if (item != null && typeof item === 'object' && !Array.isArray(item)) {
    const o = /** @type {Record<string, unknown>} */ (item);
    const tsRaw = o.ts;
    let ts = null;
    if (typeof tsRaw === 'number' && Number.isFinite(tsRaw)) ts = tsRaw;
    else if (typeof tsRaw === 'string' && tsRaw.trim() !== '' && Number.isFinite(Number(tsRaw))) {
      ts = Number(tsRaw);
    }
    return { ts, text: String(o.text ?? '') };
  }
  if (item == null) return { ts: null, text: '' };
  return { ts: null, text: String(item) };
}

/**
 * Local wall-clock HH:MM:SS.
 * @param {number|null|undefined} ts epoch seconds
 * @returns {string}
 */
export function formatLogTime(ts) {
  if (ts == null || typeof ts !== 'number' || !Number.isFinite(ts)) return '';
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/**
 * @param {LogEntry|string} entryOrLine
 * @param {{ query?: string, level?: string, sinceMs?: number, nowMs?: number }} [opts]
 * @returns {boolean}
 */
export function entryMatchesLogFilter(entryOrLine, opts = {}) {
  const entry = normalizeLogEntry(entryOrLine);
  const query = String(opts.query ?? '').trim();
  const level = String(opts.level ?? '').trim().toLowerCase();
  const sinceMs = opts.sinceMs;
  const nowMs = typeof opts.nowMs === 'number' ? opts.nowMs : Date.now();

  if (sinceMs != null && sinceMs > 0) {
    if (entry.ts == null || !Number.isFinite(entry.ts)) return false;
    if (nowMs - entry.ts * 1000 > sinceMs) return false;
  }

  if (level && level !== 'all') {
    if (inferLogLevel(entry.text) !== level) return false;
  }
  if (query) {
    if (!entry.text.toLowerCase().includes(query.toLowerCase())) return false;
  }
  return true;
}

/**
 * @param {string} line
 * @param {{ query?: string, level?: string, sinceMs?: number, nowMs?: number }} [opts]
 * @returns {boolean}
 */
export function lineMatchesLogFilter(line, opts = {}) {
  return entryMatchesLogFilter(line, opts);
}

/**
 * @param {Array<LogEntry|string>} entries
 * @param {{ query?: string, level?: string, sinceMs?: number, nowMs?: number }} [opts]
 * @returns {LogEntry[]}
 */
export function filterLogEntries(entries, opts = {}) {
  const arr = Array.isArray(entries) ? entries : [];
  return arr
    .map(normalizeLogEntry)
    .filter((e) => entryMatchesLogFilter(e, opts));
}

/**
 * @param {string[]} lines
 * @param {{ query?: string, level?: string, sinceMs?: number, nowMs?: number }} [opts]
 * @returns {string[]}
 */
export function filterLogLines(lines, opts = {}) {
  return filterLogEntries(lines, opts).map((e) => e.text);
}

/**
 * @param {string|LogEntry} lineOrEntry
 * @returns {string}
 */
export function logLineClass(lineOrEntry) {
  const text = typeof lineOrEntry === 'object' && lineOrEntry != null
    ? String(/** @type {LogEntry} */ (lineOrEntry).text ?? '')
    : String(lineOrEntry ?? '');
  return `log-level-${inferLogLevel(text)}`;
}

export { LEVEL_ORDER };
