import { state } from './state.js';
import { api } from './api.js';
import { escapeHtml } from './utils.js';

const SEVERITY_LABELS = { info: '信息', success: '完成', warning: '警告', error: '错误' };
const PAGE_SIZE = 100;
let _stream = null;
let _streamRetry = null;
let _streamCursor = 0;
let _initialized = false;
let _filter = 'all';
let _loading = false;
let _loadingMore = false;
let _hasMore = false;
let _totalCount = 0;
let _loadedServerCount = 0;
let _loadGeneration = 0;
let _totalKnown = false;
let _refreshPending = false;

function _notificationId() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function _dedupeKey(value) {
  const raw = String(value || '');
  if (raw.length <= 300) return raw || null;
  let hash = 2166136261;
  for (let index = 0; index < raw.length; index += 1) {
    hash ^= raw.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `${raw.slice(0, 285)}:${(hash >>> 0).toString(16)}`;
}

function _matchesFilter(notification) {
  if (_filter === 'unread') return !notification.read_at;
  if (_filter === 'attention') return ['warning', 'error'].includes(notification.severity);
  return true;
}

function _upsert(notification, { adjustUnread = true } = {}) {
  if (!notification?.id) return;
  const index = state.notifications.findIndex(item => item.id === notification.id);
  const previous = index >= 0 ? state.notifications[index] : null;
  const previousUnread = Boolean(previous && !previous.read_at);
  const nextUnread = !notification.read_at;
  if (!_matchesFilter(notification)) {
    if (index >= 0) state.notifications.splice(index, 1);
    if (adjustUnread && previousUnread) state.notificationUnread = Math.max(0, state.notificationUnread - 1);
    return;
  }
  if (index >= 0) state.notifications[index] = notification;
  else state.notifications.unshift(notification);
  if (adjustUnread && index < 0 && nextUnread) state.notificationUnread += 1;
  else if (adjustUnread && index >= 0 && previousUnread !== nextUnread) {
    state.notificationUnread += nextUnread ? 1 : -1;
  }
  state.notificationUnread = Math.max(0, state.notificationUnread);
  state.notifications.sort((a, b) => (Number(b.seq) || 0) - (Number(a.seq) || 0));
}

function _time(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function _query(offset) {
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (_filter === 'unread') params.set('unread', '1');
  if (_filter === 'attention') params.set('severity', 'warning,error');
  return `/api/notifications?${params.toString()}`;
}

function _recordRevision(value, { advanceCursor = true } = {}) {
  const revision = Number(value);
  if (!Number.isFinite(revision) || revision < 0) return 0;
  state.notificationLatestSeq = Math.max(state.notificationLatestSeq, revision);
  if (advanceCursor) _streamCursor = Math.max(_streamCursor, revision);
  return revision;
}

function _mergePage(result, append) {
  const items = Array.isArray(result?.notifications) ? result.notifications : [];
  const revision = Number(result?.latest_seq);
  // A mutation or SSE refresh may have advanced the local state while this
  // request was in flight. Never let an older snapshot overwrite it.
  if (Number.isFinite(revision) && revision < state.notificationLatestSeq) return;
  const merged = append ? new Map(state.notifications.map(item => [item.id, item])) : new Map();
  items.forEach(item => merged.set(item.id, item));
  state.notifications = [...merged.values()].filter(_matchesFilter);
  state.notifications.sort((a, b) => (Number(b.seq) || 0) - (Number(a.seq) || 0));
  const unread = Number(result?.unread_count);
  if (Number.isFinite(unread)) state.notificationUnread = Math.max(0, unread);
  _recordRevision(revision);
  const total = Number(result?.total_count);
  _totalKnown = Number.isFinite(total);
  _totalCount = _totalKnown ? Math.max(0, total) : state.notifications.length;
  // Use the number currently present rather than the number returned by the
  // previous page. Read-state changes and live inserts can shift OFFSET rows.
  _loadedServerCount = state.notifications.length;
  _hasMore = _totalKnown && _loadedServerCount < _totalCount;
}

function _render() {
  const panel = document.getElementById('notification-panel');
  const button = document.getElementById('btn-notifications');
  if (button) {
    button.setAttribute('aria-label', state.notificationUnread ? `通知中心，有 ${state.notificationUnread} 条未读` : '通知中心');
    button.setAttribute('aria-expanded', panel && !panel.hidden ? 'true' : 'false');
    button.classList.toggle('has-unread', state.notificationUnread > 0);
    const badge = button.querySelector('.notification-badge');
    if (badge) {
      badge.textContent = state.notificationUnread > 99 ? '99+' : String(state.notificationUnread);
      badge.hidden = state.notificationUnread === 0;
    }
  }
  if (!panel || panel.hidden) return;
  const rows = state.notifications.map(_row).join('');
  const footer = _loadingMore
    ? '<p class="notification-loading">加载中…</p>'
    : _hasMore
      ? '<button type="button" class="notification-load-more">加载更多</button>'
      : '';
  panel.innerHTML = `<div class="notification-panel-head"><strong>通知中心</strong>
    <button type="button" class="btn-link notification-read-all" ${state.notificationUnread ? '' : 'disabled'}>全部已读</button></div>
    <div class="notification-filters" role="tablist">
      <button type="button" data-filter="all" aria-selected="${_filter === 'all'}" class="${_filter === 'all' ? 'active' : ''}">全部</button>
      <button type="button" data-filter="unread" aria-selected="${_filter === 'unread'}" class="${_filter === 'unread' ? 'active' : ''}">未读</button>
      <button type="button" data-filter="attention" aria-selected="${_filter === 'attention'}" class="${_filter === 'attention' ? 'active' : ''}">警告/错误</button>
    </div>
    <div class="notification-list">${_loading ? '<p class="notification-loading">加载中…</p>' : rows || '<p class="notification-empty">暂无通知</p>'}</div>${footer}`;
  panel.querySelector('.notification-read-all')?.addEventListener('click', markAllRead);
  panel.querySelector('.notification-load-more')?.addEventListener('click', () => {
    void loadNotifications({ append: true });
  });
  panel.querySelectorAll('[data-filter]').forEach(item => item.addEventListener('click', () => {
    _filter = item.dataset.filter || 'all';
    _loadGeneration += 1;
    _loading = false;
    _loadingMore = false;
    state.notifications = [];
    _loadedServerCount = 0;
    _totalCount = 0;
    _hasMore = false;
    _totalKnown = false;
    _render();
    void loadNotifications();
  }));
  panel.querySelectorAll('.notification-row').forEach(item => item.addEventListener('click', () => openNotification(item.dataset.notificationId)));
  panel.querySelectorAll('.notification-read-one').forEach(item => item.addEventListener('click', event => {
    event.stopPropagation();
    void markNotificationRead(item.dataset.readId);
  }));
}

function _row(notification) {
  const unread = notification.read_at ? '' : ' unread';
  const severity = notification.severity || 'info';
  const project = notification.project_name ? ` · ${notification.project_name}` : '';
  const readBtn = notification.read_at
    ? ''
    : `<button type="button" class="notification-read-one" data-read-id="${escapeHtml(notification.id)}" title="标记已读" aria-label="标记已读">✓</button>`;
  return `<div class="notification-row notification-${escapeHtml(severity)}${unread}" data-notification-id="${escapeHtml(notification.id)}">
    <span class="notification-row-icon" aria-hidden="true">${severity === 'success' ? '✓' : severity === 'error' ? '!' : severity === 'warning' ? '!' : 'i'}</span>
    <span class="notification-row-copy"><strong>${escapeHtml(notification.title || SEVERITY_LABELS[severity] || '通知')}</strong>
      <span>${escapeHtml(notification.message || '')}</span><small>${escapeHtml(SEVERITY_LABELS[severity] || severity)}${escapeHtml(project)} · ${escapeHtml(_time(notification.created_at))}</small></span>
    ${readBtn}</div>`;
}

export async function loadNotifications({ append = false } = {}) {
  if (_loading || (_loadingMore && append)) return;
  const generation = ++_loadGeneration;
  if (append) _loadingMore = true;
  else _loading = true;
  _render();
  try {
    const result = await api('GET', _query(append ? _loadedServerCount : 0));
    if (generation !== _loadGeneration) return;
    _mergePage(result, append);
  } catch {
    // The inbox is auxiliary; keep the rest of the editor available when it is unavailable.
  } finally {
    if (generation === _loadGeneration) {
      _loading = false;
      _loadingMore = false;
      _render();
      if (_refreshPending) {
        _refreshPending = false;
        void loadNotifications();
      }
    }
  }
}

function _startStream() {
  if (_stream) return;
  let url = `/api/notifications/stream?after=${encodeURIComponent(_streamCursor)}`;
  const token = sessionStorage.getItem('api_token');
  if (token) url += `&token=${encodeURIComponent(token)}`;
  try { _stream = new EventSource(url); } catch { return; }
  _stream.onmessage = event => {
    try {
      const payload = JSON.parse(event.data);
      const revision = Number(payload.seq) || 0;
      if (revision <= _streamCursor) return;
      _recordRevision(revision);
      if (payload.refresh) {
        if (_loading || _loadingMore) _refreshPending = true;
        else void loadNotifications();
      }
      else if (payload.notification) {
        _upsert(payload.notification);
        _render();
      }
    } catch { /* ignore malformed events */ }
  };
  _stream.onerror = () => {
    _stream?.close();
    _stream = null;
    if (!_streamRetry) _streamRetry = setTimeout(() => { _streamRetry = null; _startStream(); }, 3_000);
  };
}

function _contextLink(overrides = {}) {
  const params = new URLSearchParams();
  const project = overrides.projectName ?? state.currentProjectName;
  const projectDir = overrides.projectDir ?? state.currentProjectDir;
  if (project) params.set('project', project);
  if (projectDir) params.set('project_dir', projectDir);
  if (overrides.entity || state.currentEntity) params.set('entity', overrides.entity || state.currentEntity);
  if (overrides.video || state.currentVideo) params.set('video', overrides.video || state.currentVideo);
  if (overrides.day || state.currentDay) params.set('day', overrides.day || state.currentDay);
  return params.toString() ? `?${params.toString()}` : null;
}

export async function registerNotification({
  message,
  severity = 'info',
  title = '通知',
  sourceType = 'ui',
  sourceId = null,
  taskId = null,
  link = null,
  dedupeKey = null,
  data = {},
  projectName = null,
  projectDir = null,
} = {}) {
  if (!message) return null;
  const effectiveProjectName = projectName ?? state.currentProjectName;
  const effectiveProjectDir = projectDir ?? state.currentProjectDir;
  const isUiMessage = sourceType === 'ui' || sourceType === 'ui_toast' || sourceType === 'ui_status';
  const scope = String(effectiveProjectDir || effectiveProjectName || 'global').slice(0, 240);
  const effectiveDedupeKey = dedupeKey || (isUiMessage
    ? `ui:${severity}:${scope}:${String(message).slice(0, 180)}:${Math.floor(Date.now() / 5_000)}`
    : null);
  const payload = {
    message: String(message).slice(0, 4_000), severity, title: String(title).slice(0, 200), source_type: sourceType,
    source_id: sourceId || _notificationId(), task_id: taskId,
    link: link || _contextLink({ projectName: effectiveProjectName, projectDir: effectiveProjectDir }),
    dedupe_key: _dedupeKey(effectiveDedupeKey), data,
  };
  try {
    const params = new URLSearchParams();
    if (effectiveProjectName) params.set('project', effectiveProjectName);
    if (effectiveProjectDir) params.set('project_dir', effectiveProjectDir);
    const endpoint = params.toString() ? `/api/notifications?${params.toString()}` : '/api/notifications';
    const result = await api('POST', endpoint, payload);
    if (result?.notification) {
      _upsert(result.notification);
      _recordRevision(result.latest_seq ?? result.notification.seq, { advanceCursor: false });
      if (Number.isFinite(Number(result.unread_count))) state.notificationUnread = Math.max(0, Number(result.unread_count));
      _render();
      window.dispatchEvent(new CustomEvent('clio:notification-created', { detail: result.notification }));
      return result.notification;
    }
  } catch {
    // Toasts must remain useful even when the server is shutting down.
  }
  return null;
}

export async function markNotificationRead(notificationId, read = true) {
  if (!notificationId) return;
  try {
    const result = await api('POST', `/api/notifications/${encodeURIComponent(notificationId)}/${read ? 'read' : 'unread'}`, {});
    if (result?.notification) _upsert(result.notification, { adjustUnread: false });
    _recordRevision(result?.latest_seq ?? result?.notification?.seq, { advanceCursor: false });
    if (Number.isFinite(Number(result?.unread_count))) state.notificationUnread = Math.max(0, Number(result.unread_count));
    _render();
  } catch { /* ignore transient read-state failures */ }
}

export async function markAllRead() {
  try {
    const result = await api('POST', '/api/notifications/read-all', {});
    _recordRevision(result?.latest_seq, { advanceCursor: false });
    state.notifications.forEach(item => { item.read_at = item.read_at || new Date().toISOString(); });
    state.notificationUnread = Number.isFinite(Number(result?.unread_count)) ? Number(result.unread_count) : 0;
    if (_filter === 'unread') {
      state.notifications = [];
      _loadedServerCount = 0;
      _totalCount = 0;
      _hasMore = false;
      _totalKnown = false;
    }
    _render();
  } catch { /* ignore transient read-state failures */ }
}

export async function openNotification(notificationId) {
  const notification = state.notifications.find(item => item.id === notificationId);
  if (!notification) return;
  await markNotificationRead(notificationId);
  const panel = document.getElementById('notification-panel');
  if (panel) panel.hidden = true;
  const actionId = notification.data?.action_id;
  if (actionId) {
    window.dispatchEvent(new CustomEvent('clio:notification-action', { detail: { id: actionId, notification } }));
  } else if (notification.task_id) {
    const sidebar = await import('./sidebar.js');
    await sidebar.selectTasks();
    if (state.currentEntity !== 'tasks') return;
    const tasks = await import('./task-center.js');
    await tasks.selectTask(notification.task_id);
  } else if (notification.link) {
    window.location.assign(notification.link);
  }
}

export function initNotificationCenter() {
  if (_initialized) return;
  const button = document.getElementById('btn-notifications');
  const panel = document.getElementById('notification-panel');
  if (!button || !panel) return;
  _initialized = true;
  button.addEventListener('click', event => {
    event.stopPropagation();
    panel.hidden = !panel.hidden;
    if (!panel.hidden) { void loadNotifications(); _render(); }
  });
  document.addEventListener('click', event => {
    const path = event.composedPath?.() || [];
    if (path.includes(panel) || path.includes(button)) return;
    panel.hidden = true;
    _render();
  });
  window.addEventListener('clio:notification-created', _render);
  void loadNotifications().finally(_startStream);
}

export { SEVERITY_LABELS, _contextLink };
