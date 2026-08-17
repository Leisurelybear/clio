import { escapeHtml } from './utils.js';
import { registerNotification } from './notification-center.js';

const MAX_VISIBLE = 3;
const DEFAULT_DURATIONS = {
  info: 4000,
  success: 4000,
  warning: 5000,
  error: 8000,
};

let queue = [];
let _containerEnsured = false;

function getContainer() {
  const container = document.getElementById('toast-container');
  if (!container) return null;
  if (!_containerEnsured) {
    if (!container.getAttribute('role')) container.setAttribute('role', 'status');
    if (!container.getAttribute('aria-live')) container.setAttribute('aria-live', 'polite');
    if (!container.getAttribute('aria-atomic')) container.setAttribute('aria-atomic', 'true');
    _containerEnsured = true;
  }
  return container;
}

function resolveDuration(type, duration) {
  if (duration !== undefined && duration !== null) return duration;
  return DEFAULT_DURATIONS[type] ?? DEFAULT_DURATIONS.info;
}

function addToast(message, type = 'info', duration, options = {}) {
  const container = getContainer();
  const notificationSeverity = type === 'success' || type === 'error' || type === 'warning' ? type : 'info';
  let notificationPromise = Promise.resolve(null);
  if (options.persist !== false) {
    notificationPromise = registerNotification({
      message,
      severity: notificationSeverity,
      title: options.title || '通知',
      sourceType: options.sourceType || 'ui_toast',
      sourceId: options.sourceId,
      taskId: options.taskId,
      link: options.link,
      dedupeKey: options.dedupeKey,
      data: options.data,
    });
  }
  if (!container) return notificationPromise;

  const resolvedDuration = resolveDuration(type, duration);
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.dataset.duration = String(resolvedDuration);

  const icons = { success: '✓', error: '!', warning: '◌', info: 'i' };

  toast.innerHTML = `
    <span class="toast-icon">${icons[type] || 'i'}</span>
    <span class="toast-message">${escapeHtml(message)}</span>
    <button class="toast-close" type="button" aria-label="关闭">&times;</button>
  `;

  toast.querySelector('.toast-close').onclick = () => removeToast(toast);

  const visible = container.children.length;
  if (visible >= MAX_VISIBLE) {
    queue.push(toast);
  } else {
    container.appendChild(toast);
    if (resolvedDuration > 0) {
      setTimeout(() => removeToast(toast), resolvedDuration);
    }
  }
  return notificationPromise;
}

function removeToast(toast) {
  if (toast.classList.contains('removing')) return;
  toast.classList.add('removing');
  toast.addEventListener('animationend', () => {
    if (toast.parentNode) toast.parentNode.removeChild(toast);
    if (queue.length > 0) {
      const next = queue.shift();
      const container = getContainer();
      if (!container) return;
      container.appendChild(next);
      const nextDur = Number(next.dataset.duration);
      // duration=0 means sticky (never auto-dismiss); do not coerce via ||.
      const ms = Number.isFinite(nextDur) ? nextDur : DEFAULT_DURATIONS.info;
      if (ms > 0) setTimeout(() => removeToast(next), ms);
    }
  }, { once: true });
}

export { addToast, DEFAULT_DURATIONS };
