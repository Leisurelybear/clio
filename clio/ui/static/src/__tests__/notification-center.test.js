import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({ api: vi.fn() }));

let state;
let api;

class FakeEventSource {
  constructor(url) { this.url = url; }
  close() {}
}

describe('notification center', () => {
  beforeEach(async () => {
    vi.resetModules();
    ({ state } = await import('../state.js'));
    ({ api } = await import('../api.js'));
    api.mockReset();
    state.notifications = [];
    state.notificationUnread = 0;
    state.notificationLatestSeq = 0;
    document.body.innerHTML = `<span id="status"></span>
      <button id="btn-notifications"><span class="notification-badge" hidden></span></button>
      <section id="notification-panel" hidden></section>`;
    globalThis.EventSource = FakeEventSource;
  });

  it('loads persistent notifications and exposes the unread badge', async () => {
    api.mockResolvedValueOnce({
      notifications: [{ id: 'n1', seq: 4, severity: 'error', title: '导出失败', message: '磁盘不可写', read_at: null }],
      unread_count: 1,
      latest_seq: 4,
    });
    const { initNotificationCenter } = await import('../notification-center.js');

    initNotificationCenter();
    await vi.waitFor(() => expect(state.notificationUnread).toBe(1));

    const badge = document.querySelector('.notification-badge');
    expect(badge.hidden).toBe(false);
    expect(badge.textContent).toBe('1');
  });

  it('registers a toast in the inbox and updates local state immediately', async () => {
    api.mockResolvedValueOnce({
      notification: {
        id: 'n2', seq: 5, severity: 'success', title: '通知', message: '裁剪完成', read_at: null,
      },
    });
    const { registerNotification } = await import('../notification-center.js');

    await registerNotification({ message: '裁剪完成', severity: 'success', sourceType: 'ui_toast' });

    expect(api).toHaveBeenCalledWith('POST', '/api/notifications', expect.objectContaining({
      message: '裁剪完成', severity: 'success', source_type: 'ui_toast',
    }));
    expect(state.notifications[0].message).toBe('裁剪完成');
    expect(state.notificationUnread).toBe(1);
  });

  it('registers error status messages even when no toast is shown', async () => {
    api.mockResolvedValue({});
    const { setStatus } = await import('../utils.js');

    setStatus('保存失败: 磁盘不可写', 'err');

    await vi.waitFor(() => expect(api).toHaveBeenCalledWith('POST', '/api/notifications', expect.objectContaining({
      message: '保存失败: 磁盘不可写', severity: 'error', source_type: 'ui_status',
    })));
  });
});
