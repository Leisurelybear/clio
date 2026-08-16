import { state } from './state.js';
import { $, escapeHtml, setStatus } from './utils.js';
import { api } from './api.js';
import { addToast } from './toast.js';

const STATUS_LABELS = {
  queued: '排队中', running: '运行中', succeeded: '已完成', failed: '失败',
  cancelling: '取消中', cancelled: '已取消', interrupted: '已中断',
};
const KIND_LABELS = {
  pipeline: '流水线', rerun: '重跑', cut_export: '裁剪导出',
  export: '剪映草稿导出', whisper_install: 'Whisper 安装', waveform: '波形生成',
};
const ACTIVE = new Set(['queued', 'running', 'cancelling']);
const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'interrupted']);
let _stream = null;
let _streamCursor = 0;
let _detailRequest = 0;
const _eventListeners = new Set();
const _taskEventSeq = new Map();

export function statusLabel(status) { return STATUS_LABELS[status] || status || '未知'; }
export function kindLabel(kind) { return KIND_LABELS[kind] || kind || '任务'; }
export function statusGroup(status) {
  if (ACTIVE.has(status)) return 'active';
  if (status === 'failed' || status === 'interrupted') return 'failed';
  if (TERMINAL.has(status)) return 'done';
  return status || 'unknown';
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
    if (filters.project !== 'all' && (task.project_name || task.project_id || '未命名') !== filters.project) return false;
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
  const project = task.project_name || task.project_id || '全局';
  return `<button type="button" class="task-row${selected}" data-task-id="${escapeHtml(task.id)}">
    <span class="task-row-main"><strong>${escapeHtml(task.title || kindLabel(task.kind))}</strong>
      <span class="task-row-meta">${escapeHtml(kindLabel(task.kind))} · ${escapeHtml(project)}</span></span>
    <span class="task-row-state task-status-${escapeHtml(status)}">${escapeHtml(statusLabel(status))}${pct ? ` · ${pct}` : ''}</span>
    <span class="task-row-time">${escapeHtml(_time(task.created_at))}</span>
  </button>`;
}

function _detailHtml(detail) {
  if (!detail) return '<p class="muted task-empty-detail">选择一个任务查看详情</p>';
  const task = detail.task || detail;
  const events = detail.events || [];
  const canCancel = task.cancellable && ACTIVE.has(task.status) && !task.cancel_requested;
  const canRetry = task.status === 'failed' || task.status === 'interrupted' || task.status === 'cancelled';
  const error = task.error_message ? `<div class="task-error"><strong>错误：</strong>${escapeHtml(task.error_message)}</div>` : '';
  const timeline = events.length
    ? events.slice().reverse().map(event => `<li class="task-event task-event-${escapeHtml(event.level || 'info')}">
      <time>${escapeHtml(_time(event.created_at))}</time><span>${escapeHtml(event.message || event.type || '')}</span></li>`).join('')
    : '<li class="muted">暂无事件</li>';
  return `<div class="task-detail-head"><div><h3>${escapeHtml(task.title || kindLabel(task.kind))}</h3>
    <div class="task-detail-sub">${escapeHtml(kindLabel(task.kind))} · ${escapeHtml(task.project_name || task.project_id || '全局')} · ${escapeHtml(statusLabel(task.status))}</div></div>
    <div class="task-detail-actions">${canCancel ? '<button class="btn-secondary task-cancel">取消</button>' : ''}${canRetry ? '<button class="btn-primary task-retry">重试</button>' : ''}</div></div>
    ${_progress(task)}<div class="task-phase">${escapeHtml(task.phase || task.message || '')}</div>${error}
    <dl class="task-meta-grid"><dt>创建</dt><dd>${escapeHtml(_time(task.created_at))}</dd><dt>开始</dt><dd>${escapeHtml(_time(task.started_at))}</dd><dt>结束</dt><dd>${escapeHtml(_time(task.finished_at))}</dd><dt>任务 ID</dt><dd class="mono">${escapeHtml(task.id)}</dd></dl>
    <h4>执行事件</h4><ol class="task-events">${timeline}</ol>`;
}

function _render() {
  const pane = $('tab-tasks');
  if (!pane) return;
  const filtered = filterTasks(state.tasks);
  const projects = [...new Set(state.tasks.map(task => task.project_name || task.project_id || '未命名'))].sort();
  const activeCount = state.tasks.filter(task => ACTIVE.has(task.status)).length;
  const projectOptions = ['<option value="all">所有项目</option>', ...projects.map(project => `<option value="${escapeHtml(project)}" ${state.taskFilters.project === project ? 'selected' : ''}>${escapeHtml(project)}</option>`)].join('');
  const kindOptions = ['<option value="all">所有类型</option>', ...Object.entries(KIND_LABELS).map(([key, label]) => `<option value="${key}" ${state.taskFilters.kind === key ? 'selected' : ''}>${label}</option>`)].join('');
  pane.innerHTML = `<div class="task-center-toolbar"><div class="task-center-title">任务中心 ${activeCount ? `<span class="task-count-badge">${activeCount}</span>` : ''}</div>
    <select class="task-filter-status"><option value="all">全部状态</option><option value="active">进行中</option><option value="failed">失败/中断</option><option value="done">已完成</option></select>
    <select class="task-filter-kind">${kindOptions}</select><select class="task-filter-project">${projectOptions}</select>
    <button type="button" class="btn-secondary task-refresh">刷新</button></div>
    <div class="task-center-layout"><div class="task-list">${filtered.length ? filtered.map(_taskRow).join('') : '<p class="muted task-empty">暂无任务</p>'}</div><section class="task-detail">${_detailHtml(state.taskDetail)}</section></div>`;
  pane.querySelector('.task-filter-status').value = state.taskFilters.status;
  pane.querySelector('.task-filter-status').onchange = event => { state.taskFilters.status = event.target.value; _render(); };
  pane.querySelector('.task-filter-kind').onchange = event => { state.taskFilters.kind = event.target.value; _render(); };
  pane.querySelector('.task-filter-project').onchange = event => { state.taskFilters.project = event.target.value; _render(); };
  pane.querySelector('.task-refresh').onclick = () => loadTasks();
  pane.querySelectorAll('.task-row').forEach(row => row.onclick = () => selectTask(row.dataset.taskId));
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
    _render();
  } catch (error) {
    setStatus(`任务加载失败: ${error.message}`, 'err');
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
  } catch (error) { setStatus(`任务详情加载失败: ${error.message}`, 'err'); }
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
    addToast(action === 'cancel' ? '已发送取消请求' : '已创建重试任务', 'success');
    await loadTasks();
    await selectTask(targetId);
  } catch (error) { addToast(`${action === 'cancel' ? '取消' : '重试'}失败: ${error.message}`, 'error', 6000); }
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
      else reject(new Error(task.error_message || task.message || statusLabel(task.status)));
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

export { STATUS_LABELS, KIND_LABELS };
