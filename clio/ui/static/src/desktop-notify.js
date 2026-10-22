import { state } from './state.js';
import { isDesktop } from './desktop-pick.js';

const _delivered = new Set();

function _notificationId(notification) {
  return String(notification?.id || '');
}

export function desktopNotificationsEnabled(config = state.config, globalConfig = state.configGlobal) {
  const value = config?.ui?.desktop_notifications ?? globalConfig?.ui?.desktop_notifications;
  return value === true;
}

export function shouldDeliverNativeNotification(notification, {
  enabled = desktopNotificationsEnabled(),
  hidden = globalThis.document?.visibilityState === 'hidden',
  delivered = _delivered,
} = {}) {
  const id = _notificationId(notification);
  return Boolean(enabled && hidden && id && !delivered.has(id) && String(notification?.message || '').trim());
}

async function _deliver(notification) {
  const title = String(notification?.title || 'Clio').slice(0, 160);
  const body = String(notification?.message || '').slice(0, 1200);
  if (isDesktop()) {
    const api = window.pywebview?.api;
    if (typeof api?.notify !== 'function') return false;
    const result = await api.notify(title, body);
    return result?.delivered === true;
  }
  const NotificationCtor = globalThis.Notification;
  if (typeof NotificationCtor !== 'function' || NotificationCtor.permission !== 'granted') return false;
  new NotificationCtor(title, { body, tag: _notificationId(notification) });
  return true;
}

export async function maybeDeliverDesktopNotification(notification, options = {}) {
  if (!shouldDeliverNativeNotification(notification, options)) return false;
  try {
    const delivered = await _deliver(notification);
    if (delivered) _delivered.add(_notificationId(notification));
    return delivered;
  } catch {
    return false;
  }
}

export async function requestDesktopNotificationPermission() {
  if (isDesktop()) return 'granted';
  const NotificationCtor = globalThis.Notification;
  if (typeof NotificationCtor?.requestPermission !== 'function') return 'unsupported';
  if (NotificationCtor.permission === 'granted') return 'granted';
  if (NotificationCtor.permission === 'denied') return 'denied';
  return NotificationCtor.requestPermission();
}

export function initDesktopNotificationBridge(root = globalThis.document) {
  if (!root) return () => {};
  const onCreated = event => { void maybeDeliverDesktopNotification(event.detail); };
  const onChange = event => {
    const target = event.target;
    if (target?.dataset?.path !== 'ui.desktop_notifications' || !target.checked) return;
    void requestDesktopNotificationPermission();
  };
  globalThis.window?.addEventListener('clio:notification-created', onCreated);
  root.addEventListener?.('change', onChange);
  return () => {
    globalThis.window?.removeEventListener('clio:notification-created', onCreated);
    root.removeEventListener?.('change', onChange);
  };
}

export function _resetDesktopNotificationStateForTests() {
  _delivered.clear();
}
