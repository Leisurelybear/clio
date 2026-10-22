import { state } from './state.js';
import { $, escapeHtml, setStatus } from './utils.js';
import { api } from './api.js';
import { addToast } from './toast.js';
import { onLocaleChange, t } from './i18n.js';

const STATUS_KEYS = ['queued', 'running', 'succeeded', 'failed', 'cancelling', 'cancelled', 'interrupted'];
const KIND_KEYS = ['pipeline', 'rerun', 'cut_export', 'export', 'whisper_install', 'ffmpeg_install', 'waveform'];
const ACTIVE = new Set(['queued', 'running', 'cancelling']);
const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'interrupted']);
let _stream = null;
let _streamCursor = 0;
let _detailRequest = 0;
const _eventListeners = new Set();
const _taskEventSeq = new Map();

export function statusLabel(status) {
  return STATUS_KEYS.includes(status) ? t(`task.status.${status}`) : (status || t('task.unknown'));
}
export function kindLabel(kind) {
  return KIND_KEYS.includes(kind) ? t(`task.kind.${kind}`) : (kind || t('task.fallback_kind'));
}
export function statusGroup(status) {
  if (ACTIVE.has(status)) return 'active';
  if (status === 'failed' || status === 'interrupted') return 'failed';
  if (TERMINAL.has(status)) return 'done';
  return status || 'unknown';
}

export function isTaskRetryable(task) {
  if (!task || task.input_summary?.retryable === false) return false;
  return task.status === 'failed' || task.status === 'interrupted' || task.status === 'cancelled';
}

function _updateNavBadge() {
  const badge = document.querySelector('.project-item[data-entity="tasks"] .task-nav-badge');
  if (!badge) return;
  const activeCount = state.tasks.filter(task => ACTIVE.has(task.status)).length;
  badge.textContent = activeCount > 99 ? '99+' : String(activeCount);
  badge.hidden = activeCount === 0;
}

function _taskUrl(taskId) {
  return `/api/tasks/${encodeURIComponent(taskId)}`;
}

function _upsertTask(task) {
  if (!task?.id) return;
  const index = state.tasks.findIndex(item => item.id === task.id);
  if (index >= 0) state.tasks[index] = task;
  else state.tasks.unshift(task);
  state.tasks.sort((a, b) => String(b.updated_at || b.heartbeat_at || b.created_at || '')
    .localeCompare(String(a.updated_at || a.heartbeat_at || a.created_at || '')));
  if (state.tasks.length > 200) state.tasks.length = 200;
}

export function filterTasks(tasks, filters = state.taskFilters) {
  return (tasks || []).filter(task => {
    if (filters.status !== 'all' && statusGroup(task.status) !== filters.status) return false;
    if (filters.kind !== 'all' && task.kind !== filters.kind) return false;
    if (filters.project !== 'all' && (task.project_name || task.project_id || t('task.unnamed')) !== filters.project) return false;
    return true;
  });
}

function _time(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function _progress(task) {
  const pct = Number.isFinite(task.progress_pct) ? task.progress_pct : null;
  const width = pct == null ? (ACTIVE.has(task.status) ? 35 : 0) : Math.max(0, Math.min(100, pct));
  return `<div class="task-progress"><div class="task-progress-fill" style="width:${width}%"></div></div>`;
}

function _taskRow(task) {
  const selected = task.id === state.selectedTaskId ? ' selected' : '';
  const status = task.status || 'unknown';
  const pct = Number.isFinite(task.progress_pct) ? `${Math.round(task.progress_pct)}%` : '';
  const project = task.project_name || task.project_id || t('task.global');
  return `<button type="button" class="task-row${selected}" data-task-id="${escapeHtml(task.id)}">
    <span class="task-row-main"><strong>${escapeHtml(task.title || kindLabel(task.kind))}</strong>
      <span class="task-row-meta">${escapeHtml(kindLabel(task.kind))} · ${escapeHtml(project)}</span></span>
    <span class="task-row-state task-status-${escapeHtml(status)}">${escapeHtml(statusLabel(status))}${pct ? ` · ${pct}` : ''}</span>
    <span class="task-row-time">${escapeHtml(_time(task.created_at))}</span>
  </button>`;
}

function _detailHtml(detail) {
  if (!detail) return `<p class="muted task-empty-detail">${t('task.empty_detail')}</p>`;
  const task = detail.task || detail;
  const events = detail.events || [];
  const canCancel = task.cancellable && ACTIVE.has(task.status) && !task.cancel_requested;
  const canRetry = isTaskRetryable(task);
  const retryBlocked = task.input_summary?.retryable === false && TERMINAL.has(task.status);
  const retryNote = retryBlocked
    ? `<p class="muted">${escapeHtml(task.input_summary.retry_blocked_reason || t('task.retry_blocked'))}</p>`
    : '';
  const error = task.error_message ? `<div class="task-error"><strong>${escapeHtml(t('task.error_label'))}</strong>${escapeHtml(task.error_message)}</div>` : '';
  const timeline = events.length
    ? events.slice().reverse().map(event => `<li class="task-event task-event-${escapeHtml(event.level || 'info')}">
      <time>${escapeHtml(_time(event.created_at))}</time><span>${escapeHtml(event.message || event.type || '')}</span></li>`).join('')
    : `<li class="muted">${t('task.no_events')}</li>`;
  return `<div class="task-detail-head"><div><h3>${escapeHtml(task.title || kindLabel(task.kind))}</h3>
    <div class="task-detail-sub">${escapeHtml(kindLabel(task.kind))} · ${escapeHtml(task.project_name || task.project_id || t('task.global'))} · ${escapeHtml(statusLabel(task.status))}</div></div>
    <div class="task-detail-actions">${canCancel ? `<button class="btn-secondary task-cancel">${t('common.cancel')}</button>` : ''}${canRetry ? `<button class="btn-primary task-retry">${t('common.retry')}</button>` : ''}</div></div>
    ${_progress(task)}<div class="task-phase">${escapeHtml(task.phase || task.message || '')}</div>${error}${retryNote}
    <dl class="task-meta-grid"><dt>${t('task.created')}</dt><dd>${escapeHtml(_time(task.created_at))}</dd><dt>${t('task.started')}</dt><dd>${escapeHtml(_time(task.started_at))}</dd><dt>${t('task.finished')}</dt><dd>${escapeHtml(_time(task.finished_at))}</dd><dt>${t('task.id_label')}</dt><dd class="mono">${escapeHtml(task.id)}</dd></dl>
    <h4>${t('task.events')}</h4><ol class="task-events">${timeline}</ol>`;
}

function _render() {
  const pane = $('tab-tasks');
  if (!pane) return;
  const filtered = filterTasks(state.tasks);
  const projects = [...new Set(state.tasks.map(task => task.project_name || task.project_id || t('task.unnamed')))].sort();
  const activeCount = state.tasks.filter(task => ACTIVE.has(task.status)).length;
  const projectOptions = [`<option value="all">${t('task.filter.all_projects')}</option>`, ...projects.map(project => `<option value="${escapeHtml(project)}" ${state.taskFilters.project === project ? 'selected' : ''}>${escapeHtml(project)}</option>`)].join('');
  const kindOptions = [`<option value="all">${t('task.filter.all_kinds')}</option>`, ...KIND_KEYS.map(key => `<option value="${key}" ${state.taskFilters.kind === key ? 'selected' : ''}>${escapeHtml(kindLabel(key))}</option>`)].join('');
  pane.innerHTML = `<div class="task-center-toolbar"><div class="task-center-title">${t('task.center_title')} ${activeCount ? `<span class="task-count-badge">${activeCount}</span>` : ''}</div>
    <select class="task-filter-status"><option value="all">${t('task.filter.status_all')}</option><option value="active">${t('task.filter.status_active')}</option><option value="failed">${t('task.filter.status_failed')}</option><option value="done">${t('task.filter.status_done')}</option></select>
    <select class="task-filter-kind">${kindOptions}</select><select class="task-filter-project">${projectOptions}</select>
    <button type="button" class="btn-secondary task-refresh">${t('common.refresh')}</button></div>
    <div class="task-center-layout"><div class="task-list">${filtered.length ? filtered.map(_taskRow).join('') : `<p class="muted task-empty">${t('task.empty')}</p>`}</div><section class="task-detail">${_detailHtml(state.taskDetail)}</section></div>`;
  pane.querySelector('.task-filter-status').value = state.taskFilters.status;
  pane.querySelector('.task-filter-status').onchange = event => { state.taskFilters.status = event.target.value; _render(); };
  pane.querySelector('.task-filter-kind').onchange = event => { state.taskFilters.kind = event.target.value; _render(); };
  pane.querySelector('.task-filter-project').onchange = event => { state.taskFilters.project = event.target.value; _render(); };
  pane.querySelector('.task-refresh').onclick = () => loadTasks();
  pane.querySelectorAll('.task-row').forEach(row => row.onclick = () => selectTask(row.dataset.taskId));
  _updateNavBadge();
  pane.querySelector('.task-cancel')?.addEventListener('click', () => mutateTask('cancel'));
  pane.querySelector('.task-retry')?.addEventListener('click', () => mutateTask('retry'));
}

export async function loadTasks() {
  try {
    const result = await api('GET', '/api/tasks?visibility=all&limit=200');
    const snapshotSeq = Number(result?.latest_seq) || 0;
    const liveTasks = state.tasks.filter(task => (_taskEventSeq.get(task.id) || 0) > snapshotSeq);
    state.tasks = Array.isArray(result?.tasks) ? result.tasks : [];
    liveTasks.forEach(_upsertTask);
    state.taskLatestSeq = Math.max(state.taskLatestSeq, snapshotSeq);
    _streamCursor = Math.max(_streamCursor, state.taskLatestSeq);
    for (const [taskId, seq] of _taskEventSeq) {
      if (seq <= snapshotSeq) _taskEventSeq.delete(taskId);
    }
    if (state.selectedTaskId && !state.tasks.some(task => task.id === state.selectedTaskId)) {
      state.selectedTaskId = null;
      state.taskDetail = null;
    }
    _updateNavBadge();
    _render();
  } catch (error) {
    setStatus(t('task.load_failed', { detail: error.message }), 'err');
  }
}

export async function selectTask(taskId) {
  if (state.selectedTaskId !== taskId) state.taskDetail = null;
  state.selectedTaskId = taskId;
  _render();
  const request = ++_detailRequest;
  try {
    const result = await api('GET', `${_taskUrl(taskId)}?event_limit=300`);
    if (request !== _detailRequest) return;
    state.taskDetail = result;
    _render();
  } catch (error) { setStatus(t('task.detail_load_failed', { detail: error.message }), 'err'); }
}

/** Fetch one task snapshot for consumers that may subscribe after a fast task finished. */
export async function fetchTask(taskId) {
  if (!taskId) return null;
  const result = await api('GET', `${_taskUrl(taskId)}?event_limit=1`);
  return result?.task || null;
}

async function mutateTask(action) {
  const id = state.selectedTaskId;
  if (!id) return;
  try {
    const result = await api('POST', `${_taskUrl(id)}/${action}`, {});
    let targetId = id;
    if (result?.task) {
      _upsertTask(result.task);
      targetId = result.task.id;
      state.selectedTaskId = targetId;
      const events = action === 'retry' ? [] : (state.taskDetail?.events || []);
      state.taskDetail = { task: result.task, events };
    }
    addToast(action === 'cancel' ? t('task.cancel_requested') : t('task.retry_created'), 'success');
    await loadTasks();
    await selectTask(targetId);
  } catch (error) {
    const actionLabel = action === 'cancel' ? t('common.cancel') : t('common.retry');
    addToast(t('task.action_failed', { action: actionLabel, detail: error.message }), 'error', 6000);
  }
}

export function startTaskStream() {
  if (_stream) return;
  let url = `/api/tasks/stream?after=${encodeURIComponent(_streamCursor)}`;
  const token = sessionStorage.getItem('api_token');
  if (token) url += `&token=${encodeURIComponent(token)}`;
  try { _stream = new EventSource(url); } catch { return; }
  _stream.onmessage = event => {
    try {
      const payload = JSON.parse(event.data);
      const seq = Number(payload.seq) || 0;
      if (seq <= _streamCursor) return;
      if (seq > _streamCursor) _streamCursor = seq;
      if (payload.task) {
        _taskEventSeq.set(payload.task.id, seq);
        _upsertTask(payload.task);
        state.taskLatestSeq = Math.max(state.taskLatestSeq, seq);
        if (state.taskDetail?.task?.id === payload.task.id) {
          const events = [...(state.taskDetail.events || [])];
          if (payload.event && !events.some(item => item.seq === payload.event.seq)) {
            events.push(payload.event);
            if (events.length > 300) events.splice(0, events.length - 300);
          }
          state.taskDetail = { ...state.taskDetail, task: payload.task, events };
        }
        _updateNavBadge();
        if (state.currentEntity === 'tasks') _render();
      }
      _eventListeners.forEach(listener => {
        try { listener(payload); } catch { /* one consumer must not break the stream */ }
      });
    } catch { /* ignore malformed event */ }
  };
}

/** Subscribe to the process-wide task event stream without owning its EventSource. */
export function subscribeTaskEvents(listener) {
  if (typeof listener !== 'function') return () => {};
  _eventListeners.add(listener);
  startTaskStream();
  return () => _eventListeners.delete(listener);
}

/** Subscribe to one task with a snapshot-first, sequence-deduplicated lifecycle. */
export async function subscribeTask(taskId, listener) {
  if (!taskId || typeof listener !== 'function') return () => {};
  let ready = false;
  let active = true;
  let lastSeq = 0;
  const buffered = [];
  const deliver = payload => {
    if (!active || payload?.task?.id !== taskId) return;
    const seq = Number(payload.seq || payload.event?.seq) || 0;
    if (seq && seq <= lastSeq) return;
    if (seq) lastSeq = seq;
    listener(payload);
  };
  const unsubscribe = subscribeTaskEvents(payload => {
    if (payload?.task?.id !== taskId) return;
    if (!ready) buffered.push(payload);
    else deliver(payload);
  });
  try {
    const snapshot = await api('GET', `${_taskUrl(taskId)}?event_limit=300`);
    if (!active) return () => {};
    const events = Array.isArray(snapshot?.events) ? snapshot.events : [];
    lastSeq = events.reduce((max, event) => Math.max(max, Number(event?.seq) || 0), 0);
    listener({ task: snapshot?.task || null, events, snapshot: true, seq: lastSeq });
    ready = true;
    buffered.sort((a, b) => (Number(a.seq) || 0) - (Number(b.seq) || 0)).forEach(deliver);
    buffered.length = 0;
  } catch (error) {
    unsubscribe();
    throw error;
  }
  return () => { active = false; unsubscribe(); };
}

export async function waitForTask(taskId) {
  return new Promise((resolve, reject) => {
    let unsubscribe = () => {};
    let settled = false;
    const onTask = payload => {
      const task = payload?.task;
      if (!task || !TERMINAL.has(task.status) || settled) return;
      settled = true;
      unsubscribe();
      if (task.status === 'succeeded') resolve(task);
      else {
        const error = new Error(task.error_message || task.message || statusLabel(task.status));
        error.taskBacked = true;
        error.taskId = task.id;
        reject(error);
      }
    };
    subscribeTask(taskId, onTask).then(stop => {
      unsubscribe = stop;
      if (settled) stop();
    }).catch(reject);
  });
}

export function renderTasks() {
  startTaskStream();
  _render();
  if (!state.tasks.length) loadTasks();
}

onLocaleChange(() => {
  if (state.currentEntity === 'tasks') _render();
});

export { STATUS_KEYS, KIND_KEYS };
