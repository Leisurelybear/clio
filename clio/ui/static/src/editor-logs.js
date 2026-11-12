/**
 * Logs pane: live polling, historical source switching, filtering, and rendering.
 */
import { api } from './api.js';
import { $, escapeHtml } from './utils.js';
import { t } from './i18n.js';
import {
  entryMatchesLogFilter,
  filterLogEntries,
  inferLogLevel,
  logLineClass,
  normalizeLogEntry,
  formatLogTime,
} from './logs-filter.js';
import { LOGS_BUFFER_MAX, appendLogEntries } from './logs-buffer.js';
import {
  formatLogHour,
  historyStatusText,
  logFileLabel,
  sortLogFiles,
} from './logs-history.js';

let _logsTimer = null;
let _logsOffset = 0;
let _logsAutoScroll = true;
/** @type {Array<{ts: number|null, text: string}>} */
let _logsBuffer = [];
/** Persist across re-open of the logs panel (prefer keep filters after clear). */
let _logsFilterQuery = '';
/** @type {''|'all'|'info'|'warn'|'error'} */
let _logsFilterLevel = 'all';
/** 0 = all; else window in ms */
let _logsFilterSinceMs = 0;
/** Poll generation currently in flight; prevents overlap without blocking a new mode. */
let _logsPollInFlightGen = null;
/** Invalidate in-flight polls when leaving/re-entering live mode. */
let _logsPollGeneration = 0;
/** Empty means live session; otherwise an hourly log basename. */
let _logsSource = '';
/** @type {Array<{ts: number|null, text: string}>} */
let _logsHistoryBuffer = [];
let _logsHistoryTruncated = false;
/** Ignore stale historical-file responses after switching sources. */
let _logsHistorySeq = 0;

const _LOG_LEVELS = ['debug', 'info', 'warn', 'error'];

function _levelLabel(level) {
  return _LOG_LEVELS.includes(level) ? t(`logs.level.${level}`) : t('logs.level.info');
}

const _SINCE_CHIPS = [
  { ms: 0, key: 'logs.since.all' },
  { ms: 5 * 60 * 1000, key: 'logs.since.5m' },
  { ms: 15 * 60 * 1000, key: 'logs.since.15m' },
  { ms: 60 * 60 * 1000, key: 'logs.since.60m' },
];

function _logsFilterOpts() {
  return {
    query: _logsFilterQuery,
    level: _logsFilterLevel,
    sinceMs: _logsFilterSinceMs || undefined,
  };
}

function _activeLogsBuffer() {
  return _logsSource ? _logsHistoryBuffer : _logsBuffer;
}

function _appendLogLineEl(view, entryOrLine) {
  const entry = normalizeLogEntry(entryOrLine);
  const level = inferLogLevel(entry.text);
  const d = document.createElement('div');
  d.className = `log-line ${logLineClass(entry)}`;
  d.dataset.level = level;
  const badge = document.createElement('span');
  badge.className = 'log-level-badge';
  badge.textContent = _levelLabel(level);
  const timeEl = document.createElement('span');
  timeEl.className = 'log-line-time';
  const clock = formatLogTime(entry.ts);
  timeEl.textContent = clock || '--:--:--';
  if (entry.ts != null) {
    timeEl.title = new Date(entry.ts * 1000).toLocaleString();
  }
  const text = document.createElement('span');
  text.className = 'log-line-text';
  text.textContent = entry.text;
  d.appendChild(badge);
  d.appendChild(timeEl);
  d.appendChild(text);
  view.appendChild(d);
}

function _paintLogsView(view) {
  if (!view) return;
  const buffer = _activeLogsBuffer();
  const visible = filterLogEntries(buffer, _logsFilterOpts());
  view.innerHTML = '';
  for (const entry of visible) {
    _appendLogLineEl(view, entry);
  }
  const empty = $('logs-empty-hint');
  if (empty) {
    const filteredOut = buffer.length > 0 && visible.length === 0;
    empty.hidden = !filteredOut;
  }
  const countEl = $('logs-match-count');
  if (countEl) {
    countEl.textContent = buffer.length
      ? t('logs.match_count', { visible: visible.length, total: buffer.length })
      : '';
  }
  if (_logsAutoScroll) view.scrollTop = view.scrollHeight;
}

function _syncLogsSourceUi(statusText = '') {
  const select = $('logs-source-select');
  if (select) select.value = _logsSource;

  const clearBtn = $('btn-logs-clear');
  if (clearBtn) {
    clearBtn.disabled = Boolean(_logsSource);
    clearBtn.title = _logsSource
      ? t('logs.clear_title_history')
      : t('logs.clear_title_live');
  }

  const status = $('logs-source-status');
  if (status) {
    status.textContent = statusText || (
      _logsSource
        ? historyStatusText({
          name: _logsSource,
          logs: _logsHistoryBuffer,
          truncated: _logsHistoryTruncated,
        })
        : t('logs.source_live')
    );
  }
}

function _syncLogsLevelChips() {
  document.querySelectorAll('[data-log-level]').forEach((btn) => {
    const on = (btn.dataset.logLevel || '') === _logsFilterLevel;
    btn.classList.toggle('is-active', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

function _syncLogsSinceChips() {
  document.querySelectorAll('[data-log-since]').forEach((btn) => {
    const ms = Number(btn.dataset.logSince || 0);
    const on = ms === _logsFilterSinceMs;
    btn.classList.toggle('is-active', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

/** Stop session-log polling without clearing the in-memory buffer (P2-P38). */
export function stopLogsPolling() {
  if (_logsTimer) {
    clearInterval(_logsTimer);
    _logsTimer = null;
  }
  _logsPollInFlightGen = null;
  _logsPollGeneration += 1;
  _logsHistorySeq += 1;
}

export function renderLogs() {
  const pane = $('tab-logs');
  stopLogsPolling();
  const sinceChipsHtml = _SINCE_CHIPS.map((c) =>
    `<button type="button" class="logs-chip" data-log-since="${c.ms}">${escapeHtml(t(c.key))}</button>`
  ).join('');
  pane.innerHTML = `
    <div class="logs-toolbar">
      <span class="logs-toolbar-title">${t('logs.title')}</span>
      <select id="logs-source-select" class="logs-source-select" aria-label="${t('logs.source_label')}">
        <option value="">${t('logs.source_current')}</option>
      </select>
      <span id="logs-source-status" class="logs-source-status muted" role="status"></span>
      <input type="search" id="logs-filter-query" class="logs-filter-input"
        placeholder="${t('logs.filter_placeholder')}" autocomplete="off"
        value="${escapeHtml(_logsFilterQuery)}"
        aria-label="${t('logs.filter_aria')}">
      <div class="logs-level-chips" role="group" aria-label="${t('logs.level_aria')}">
        <button type="button" class="logs-chip" data-log-level="all">${t('logs.level.all')}</button>
        <button type="button" class="logs-chip" data-log-level="info">${t('logs.level.info')}</button>
        <button type="button" class="logs-chip" data-log-level="warn">${t('logs.level.warn')}</button>
        <button type="button" class="logs-chip" data-log-level="error">${t('logs.level.error')}</button>
      </div>
      <div class="logs-since-chips" role="group" aria-label="${t('logs.since_aria')}">
        ${sinceChipsHtml}
      </div>
      <span id="logs-match-count" class="logs-match-count muted"></span>
      <label class="logs-autoscroll-label">
        <input type="checkbox" id="logs-autoscroll" ${_logsAutoScroll ? 'checked' : ''}> ${t('logs.autoscroll')}
      </label>
      <button type="button" class="btn-secondary" id="btn-logs-clear">${t('logs.clear')}</button>
    </div>
    <p id="logs-empty-hint" class="logs-empty-hint muted" hidden>${t('logs.empty_filtered')}</p>
    <div id="logs-view" class="logs-view" role="log" aria-live="polite"></div>
  `;
  const view = $('logs-view');
  const cb = $('logs-autoscroll');
  if (cb) cb.onchange = () => { _logsAutoScroll = cb.checked; };
  const queryInput = $('logs-filter-query');
  if (queryInput) {
    queryInput.oninput = () => {
      _logsFilterQuery = queryInput.value || '';
      _paintLogsView(view);
    };
  }
  pane.querySelectorAll('[data-log-level]').forEach((btn) => {
    btn.onclick = () => {
      _logsFilterLevel = btn.dataset.logLevel || 'all';
      _syncLogsLevelChips();
      _paintLogsView(view);
    };
  });
  pane.querySelectorAll('[data-log-since]').forEach((btn) => {
    btn.onclick = () => {
      _logsFilterSinceMs = Number(btn.dataset.logSince || 0);
      _syncLogsSinceChips();
      _paintLogsView(view);
    };
  });
  _syncLogsLevelChips();
  _syncLogsSinceChips();

  $('btn-logs-clear').onclick = async () => {
    if (_logsSource) return;
    try {
      await api('POST', '/api/logs/clear', {});
      _logsBuffer = [];
      _logsOffset = 0;
      _paintLogsView(view);
      _syncLogsSourceUi();
      void refreshSourceOptions();
    } catch { /* ignore */ }
  };

  const ingest = (lines) => {
    if (!Array.isArray(lines) || !lines.length) return;
    const prevLen = _logsBuffer.length;
    const entries = lines.map(normalizeLogEntry);
    appendLogEntries(_logsBuffer, entries);
    const trimmed = prevLen + entries.length > LOGS_BUFFER_MAX;
    // After ring trim, incremental DOM append would be wrong — full repaint.
    if (trimmed) {
      _paintLogsView(view);
      return;
    }
    for (const entry of entries) {
      if (entryMatchesLogFilter(entry, _logsFilterOpts())) {
        _appendLogLineEl(view, entry);
      }
    }
    const empty = $('logs-empty-hint');
    if (empty) {
      const visible = filterLogEntries(_logsBuffer, _logsFilterOpts());
      empty.hidden = !(_logsBuffer.length > 0 && visible.length === 0);
    }
    const countEl = $('logs-match-count');
    if (countEl) {
      const visible = filterLogEntries(_logsBuffer, _logsFilterOpts());
      countEl.textContent = t('logs.match_count', { visible: visible.length, total: _logsBuffer.length });
    }
    if (_logsAutoScroll) view.scrollTop = view.scrollHeight;
  };

  const pollOnce = async () => {
    if (_logsPollInFlightGen === _logsPollGeneration) return;
    const generation = _logsPollGeneration;
    _logsPollInFlightGen = generation;
    try {
      const r = await api('GET', `/api/logs?offset=${_logsOffset}`);
      if (!r || !r.logs) return;
      // Left the logs pane while in flight — do not touch detached DOM.
      if (!$('logs-view') || generation !== _logsPollGeneration || _logsSource) return;
      ingest(r.logs);
      if (typeof r.total === 'number') _logsOffset = r.total;
    } catch { /* ignore */ } finally {
      if (_logsPollInFlightGen === generation) _logsPollInFlightGen = null;
    }
  };

  const startLivePolling = () => {
    stopLogsPolling();
    if (_logsSource) return;
    _logsTimer = setInterval(pollOnce, 2000);
    void pollOnce();
  };

  const switchToLive = () => {
    stopLogsPolling();
    _logsSource = '';
    _logsHistoryBuffer = [];
    _logsHistoryTruncated = false;
    _syncLogsSourceUi();
    _paintLogsView(view);
    startLivePolling();
  };

  const loadHistoryFile = async (name) => {
    stopLogsPolling();
    const seq = ++_logsHistorySeq;
    _logsSource = name;
    _logsHistoryBuffer = [];
    _logsHistoryTruncated = false;
    _syncLogsSourceUi(t('logs.loading_history'));
    _paintLogsView(view);
    try {
      const r = await api('GET', `/api/logs/file?name=${encodeURIComponent(name)}`);
      if (seq !== _logsHistorySeq || !$('logs-view')) return;
      _logsHistoryBuffer = Array.isArray(r?.logs)
        ? r.logs.map(normalizeLogEntry)
        : [];
      _logsHistoryTruncated = Boolean(r?.truncated);
      _syncLogsSourceUi();
      _paintLogsView(view);
    } catch {
      if (seq !== _logsHistorySeq || !$('logs-view')) return;
      _logsHistoryBuffer = [];
      _logsHistoryTruncated = false;
      _syncLogsSourceUi(t('logs.history_load_failed'));
      _paintLogsView(view);
    }
  };

  const refreshSourceOptions = async () => {
    try {
      const r = await api('GET', '/api/logs/files');
      const select = $('logs-source-select');
      if (!select) return;
      const files = sortLogFiles(r?.files);
      const options = [`<option value="">${escapeHtml(t('logs.source_current'))}</option>`];
      for (const file of files) {
        options.push(
          `<option value="${escapeHtml(file.name)}">${escapeHtml(logFileLabel(file))}</option>`
        );
      }
      const currentMissing = _logsSource && !files.some((file) => file.name === _logsSource);
      if (currentMissing) {
        options.push(
          `<option value="${escapeHtml(_logsSource)}">${escapeHtml(t('logs.source_unavailable', { name: formatLogHour(_logsSource) }))}</option>`
        );
      }
      select.innerHTML = options.join('');
      select.value = _logsSource;
      _syncLogsSourceUi(currentMissing ? t('logs.history_missing') : '');
    } catch {
      if ($('logs-source-status')) _syncLogsSourceUi(t('logs.history_list_failed'));
    }
  };

  const sourceSelect = $('logs-source-select');
  if (sourceSelect) {
    sourceSelect.onchange = () => {
      const selected = sourceSelect.value || '';
      if (!selected) {
        switchToLive();
      } else {
        void loadHistoryFile(selected);
      }
    };
  }

  // Re-enter: show the retained source immediately. Live mode resumes from offset;
  // historical mode stays paused and read-only.
  _paintLogsView(view);
  _syncLogsSourceUi();
  void refreshSourceOptions();
  if (!_logsSource) startLivePolling();
}
