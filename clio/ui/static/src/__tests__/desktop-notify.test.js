import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { state } from '../state.js';
import {
  _resetDesktopNotificationStateForTests,
  desktopNotificationsEnabled,
  initDesktopNotificationBridge,
  maybeDeliverDesktopNotification,
  requestDesktopNotificationPermission,
  shouldDeliverNativeNotification,
} from '../desktop-notify.js';

describe('desktop notifications', () => {
  beforeEach(() => {
    _resetDesktopNotificationStateForTests();
    state.config = null;
    state.configGlobal = null;
    delete window.pywebview;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    delete globalThis.Notification;
  });

  it('reads opt-in from effective or global config', () => {
    state.config = { ui: { desktop_notifications: true } };
    expect(desktopNotificationsEnabled()).toBe(true);
    state.config = null;
    state.configGlobal = { ui: { desktop_notifications: true } };
    expect(desktopNotificationsEnabled()).toBe(true);
    expect(desktopNotificationsEnabled({}, {})).toBe(false);
  });

  it('requires an enabled hidden page with a message and unique id', () => {
    const item = { id: 'n1', message: 'done' };
    expect(shouldDeliverNativeNotification(item, { enabled: true, hidden: true })).toBe(true);
    expect(shouldDeliverNativeNotification(item, { enabled: false, hidden: true })).toBe(false);
    expect(shouldDeliverNativeNotification(item, { enabled: true, hidden: false })).toBe(false);
    expect(shouldDeliverNativeNotification({ id: 'n2', message: ' ' }, { enabled: true, hidden: true })).toBe(false);
  });

  it('delivers through the desktop bridge once', async () => {
    const notify = vi.fn().mockResolvedValue({ ok: true, delivered: true });
    window.pywebview = { api: { notify } };
    const item = { id: 'n1', title: 'Export', message: 'done' };

    expect(await maybeDeliverDesktopNotification(item, { enabled: true, hidden: true })).toBe(true);
    expect(notify).toHaveBeenCalledWith('Export', 'done');
    expect(await maybeDeliverDesktopNotification(item, { enabled: true, hidden: true })).toBe(false);
    expect(notify).toHaveBeenCalledTimes(1);
  });

  it('retries the same notification after the desktop bridge reports failure', async () => {
    const notify = vi.fn()
      .mockResolvedValueOnce({ ok: false, delivered: false })
      .mockResolvedValueOnce({ ok: true, delivered: true });
    window.pywebview = { api: { notify } };
    const item = { id: 'retry-me', title: 'Export', message: 'done' };

    expect(await maybeDeliverDesktopNotification(item, { enabled: true, hidden: true })).toBe(false);
    expect(await maybeDeliverDesktopNotification(item, { enabled: true, hidden: true })).toBe(true);

    expect(notify).toHaveBeenCalledTimes(2);
  });

  it('uses the browser Notification API only when permission is granted', async () => {
    const notify = vi.fn();
    globalThis.Notification = class {
      static permission = 'granted';
      constructor(title, options) { notify(title, options); }
    };

    const delivered = await maybeDeliverDesktopNotification(
      { id: 'n3', title: 'Build', message: 'failed' },
      { enabled: true, hidden: true },
    );

    expect(delivered).toBe(true);
    expect(notify).toHaveBeenCalledWith('Build', { body: 'failed', tag: 'n3' });
  });

  it('does not deliver when the bridge or permission rejects', async () => {
    window.pywebview = { api: { notify: vi.fn().mockRejectedValue(new Error('no toast')) } };
    expect(await maybeDeliverDesktopNotification(
      { id: 'n4', message: 'x' }, { enabled: true, hidden: true },
    )).toBe(false);

    globalThis.Notification = class { static permission = 'denied'; };
    expect(await maybeDeliverDesktopNotification(
      { id: 'n5', message: 'x' }, { enabled: true, hidden: true },
    )).toBe(false);
  });

  it('requests permission from an explicit settings change', async () => {
    const requestPermission = vi.fn().mockResolvedValue('granted');
    globalThis.Notification = class { static permission = 'default'; static requestPermission = requestPermission; };
    document.body.innerHTML = '<input type="checkbox" data-path="ui.desktop_notifications">';
    const cleanup = initDesktopNotificationBridge(document);
    const checkbox = document.querySelector('input');
    checkbox.checked = true;
    checkbox.dispatchEvent(new Event('change', { bubbles: true }));

    expect(requestPermission).toHaveBeenCalledTimes(1);
    expect(await requestDesktopNotificationPermission()).toBe('granted');
    cleanup();
  });
});
