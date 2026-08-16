import { state } from './state.js';
import { api } from './api.js';
import { escapeHtml } from './utils.js';

const SEVERITY_LABELS = { info: '信息', success: '完成', warning: '警告', error: '错误' };
let _stream = null;
let _streamRetry = null;
let _streamCursor = 0;
let _initialized = false;
let _filter = 'all';

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

function _upsert(notification) {
  if (!notification?.id) return;
  const index = state.notifications.findIndex(item => item.id === notification.id);
  const previousUnread = index >= 0 && !state.notifications[index].read_at;
  const nextUnread = !notification.read_at;
  if (index >= 0) state.notifications[index] = notification;
  else state.notifications.unshift(notification);
  if (index < 0 && nextUnread) state.notificationUnread += 1;
  else if (previousUnread !== nextUnread) state.notificationUnread += nextUnread ? 1 : -1;
  state.notificationUnread = Math.max(0, state.notificationUnread);
  state.notifications.sort((a, b) => (Number(b.seq) || 0) - (Number(a.seq) || 0));
  if (state.notifications.length > 500) state.notifications.length = 500;
}

function _time(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function _visible() {
  if (_filter === 'unread') return state.notifications.filter(item => !item.read_at);
  if (_filter === 'attention') return state.notifications.filter(item => ['warning', 'error'].includes(item.severity));
  return state.notifications;
}

function _render() {
  const panel = document.getElementById('notification-panel');
  const button = document.getElementById('btn-notifications');
  if (button) {
    button.setAttribute('aria-label', state.notificationUnread ? `通知中心，有 ${state.notificationUnread} 条未读` : '通知中心');
    button.classList.toggle('has-unread', state.notificationUnread > 0);
    const badge = button.querySelector('.notification-badge');
    if (badge) {
      badge.textContent = state.notificationUnread > 99 ? '99+' : String(state.notificationUnread);
      badge.hidden = state.notificationUnread === 0;
    }
  }
  if (!panel || panel.hidden) return;
  const items = _visible();
  panel.innerHTML = `<div class="notification-panel-head"><strong>通知中心</strong>
    <button type="button" class="btn-link notification-read-all" ${state.notificationUnread ? '' : 'disabled'}>全部已读</button></div>
    <div class="notification-filters" role="tablist">
      <button type="button" data-filter="all" class="${_filter === 'all' ? 'active' : ''}">全部</button>
      <button type="button" data-filter="unread" class="${_filter === 'unread' ? 'active' : ''}">未读</button>
      <button type="button" data-filter="attention" class="${_filter === 'attention' ? 'active' : ''}">警告/错误</button>
    </div>
    <div class="notification-list">${items.length ? items.map(_row).join('') : '<p class="notification-empty">暂无通知</p>'}</div>`;
  panel.querySelector('.notification-read-all')?.addEventListener('click', markAllRead);
  panel.querySelectorAll('[data-filter]').forEach(item => item.addEventListener('click', () => {
    _filter = item.dataset.filter || 'all';
    _render();
  }));
  panel.querySelectorAll('.notification-row').forEach(item => item.addEventListener('click', () => openNotification(item.dataset.notificationId)));
}

function _row(notification) {
  const unread = notification.read_at ? '' : ' unread';
  const severity = notification.severity || 'info';
  const project = notification.project_name ? ` · ${notification.project_name}` : '';
  return `<button type="button" class="notification-row notification-${escapeHtml(severity)}${unread}" data-notification-id="${escapeHtml(notification.id)}">
    <span class="notification-row-icon" aria-hidden="true">${severity === 'success' ? '✓' : severity === 'error' ? '!' : severity === 'warning' ? '!' : 'i'}</span>
    <span class="notification-row-copy"><strong>${escapeHtml(notification.title || SEVERITY_LABELS[severity] || '通知')}</strong>
      <span>${escapeHtml(notification.message || '')}</span><small>${escapeHtml(SEVERITY_LABELS[severity] || severity)}${escapeHtml(project)} · ${escapeHtml(_time(notification.created_at))}</small></span>
    <span class="notification-unread-dot" aria-hidden="true"></span></button>`;
}

export async function loadNotifications() {
  try {
    const result = await api('GET', '/api/notifications?limit=100');
    const snapshotSeq = Number(result?.latest_seq) || 0;
    const liveItems = state.notifications.filter(item => (Number(item.seq) || 0) > snapshotSeq);
    state.notifications = Array.isArray(result?.notifications) ? result.notifications : [];
    state.notificationUnread = Number(result?.unread_count) || state.notifications.filter(item => !item.read_at).length;
    liveItems.forEach(_upsert);
    state.notificationLatestSeq = Math.max(state.notificationLatestSeq, snapshotSeq);
    _streamCursor = Math.max(_streamCursor, snapshotSeq);
    _render();
  } catch {
    // The inbox is auxiliary; keep the rest of the editor available when it is unavailable.
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
      const seq = Number(payload.seq) || 0;
      if (seq <= _streamCursor) return;
      _streamCursor = seq;
      state.notificationLatestSeq = Math.max(state.notificationLatestSeq, seq);
      _upsert(payload.notification);
      _render();
    } catch { /* ignore malformed events */ }
  };
  _stream.onerror = () => {
    _stream?.close();
    _stream = null;
    if (!_streamRetry) _streamRetry = setTimeout(() => { _streamRetry = null; _startStream(); }, 3_000);
  };
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
} = {}) {
  if (!message) return null;
  const isUiMessage = sourceType === 'ui' || sourceType === 'ui_toast' || sourceType === 'ui_status';
  const effectiveDedupeKey = dedupeKey || (isUiMessage
    ? `ui:${severity}:${String(state.currentProjectName || '').slice(0, 60)}:${String(message).slice(0, 180)}:${Math.floor(Date.now() / 5_000)}`
    : null);
  const payload = {
    message: String(message).slice(0, 4_000), severity, title: String(title).slice(0, 200), source_type: sourceType,
    source_id: sourceId || _notificationId(), task_id: taskId, link,
    dedupe_key: _dedupeKey(effectiveDedupeKey), data,
  };
  try {
    const result = await api('POST', '/api/notifications', payload);
    if (result?.notification) {
      _upsert(result.notification);
      state.notificationLatestSeq = Math.max(state.notificationLatestSeq, Number(result.notification.seq) || 0);
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
    if (result?.notification) _upsert(result.notification);
    _render();
  } catch { /* ignore transient read-state failures */ }
}

export async function markAllRead() {
  try {
    await api('POST', '/api/notifications/read-all', {});
    state.notifications.forEach(item => { item.read_at = item.read_at || new Date().toISOString(); });
    state.notificationUnread = 0;
    _render();
  } catch { /* ignore transient read-state failures */ }
}

export async function openNotification(notificationId) {
  const notification = state.notifications.find(item => item.id === notificationId);
  if (!notification) return;
  await markNotificationRead(notificationId);
  const panel = document.getElementById('notification-panel');
  if (panel) panel.hidden = true;
  if (notification.task_id) {
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
  _initialized = true;
  const button = document.getElementById('btn-notifications');
  const panel = document.getElementById('notification-panel');
  if (!button || !panel) return;
  button.addEventListener('click', event => {
    event.stopPropagation();
    panel.hidden = !panel.hidden;
    if (!panel.hidden) { loadNotifications(); _render(); }
  });
  document.addEventListener('click', event => {
    if (!event.target.closest('#notification-panel, #btn-notifications')) panel.hidden = true;
  });
  window.addEventListener('clio:notification-created', _render);
  loadNotifications().finally(_startStream);
}

export { SEVERITY_LABELS };
