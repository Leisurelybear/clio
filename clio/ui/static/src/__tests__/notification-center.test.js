import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({ api: vi.fn() }));

let state;
let api;
let eventSource;

class FakeEventSource {
  constructor(url) { this.url = url; eventSource = this; }
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
      total_count: 1,
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

  it('persists status messages even when the status element is unavailable', async () => {
    document.body.innerHTML = '';
    api.mockResolvedValue({});
    const { setStatus } = await import('../utils.js');

    await setStatus('后台操作失败', 'err');

    expect(api).toHaveBeenCalledWith('POST', '/api/notifications', expect.objectContaining({
      message: '后台操作失败', severity: 'error', source_type: 'ui_status',
    }));
  });

  it('registers every successful status without relying on message wording', async () => {
    api.mockResolvedValue({});
    const { setStatus } = await import('../utils.js');

    await setStatus('预览播放完毕', 'ok');

    expect(api).toHaveBeenCalledWith('POST', '/api/notifications', expect.objectContaining({
      message: '预览播放完毕', severity: 'success', source_type: 'ui_status',
    }));
  });

  it('keeps the panel open when changing filters', async () => {
    api.mockResolvedValue({ notifications: [], unread_count: 0, latest_seq: 0, total_count: 0 });
    const { initNotificationCenter } = await import('../notification-center.js');
    initNotificationCenter();
    await vi.waitFor(() => expect(eventSource).toBeTruthy());
    document.getElementById('btn-notifications').click();
    await vi.waitFor(() => expect(document.querySelector('[data-filter="attention"]')).toBeTruthy());

    document.querySelector('[data-filter="attention"]').click();

    expect(document.getElementById('notification-panel').hidden).toBe(false);
    expect(api).toHaveBeenCalledWith('GET', expect.stringContaining('severity=warning%2Cerror'));
  });

  it('loads more than one page and keeps the global unread count', async () => {
    const page = Array.from({ length: 100 }, (_, index) => ({
      id: `n${index}`, seq: 100 - index, severity: 'warning', title: '提醒', message: `m${index}`, read_at: null,
    }));
    api.mockImplementation(async (method, url) => {
      if (method !== 'GET') return {};
      const offset = new URL(url, 'http://127.0.0.1').searchParams.get('offset');
      return offset === '100'
        ? { notifications: [{ id: 'n-old', seq: 1, severity: 'error', title: '旧', message: 'old', read_at: null }], unread_count: 101, latest_seq: 101, total_count: 101 }
        : { notifications: page, unread_count: 101, latest_seq: 101, total_count: 101 };
    });
    const { initNotificationCenter } = await import('../notification-center.js');
    initNotificationCenter();
    await vi.waitFor(() => expect(state.notificationUnread).toBe(101));
    document.getElementById('btn-notifications').click();
    await vi.waitFor(() => expect(document.querySelectorAll('.notification-row')).toHaveLength(100));
    expect(document.querySelector('.notification-load-more')).toBeTruthy();

    document.querySelector('.notification-load-more').click();
    await vi.waitFor(() => expect(document.querySelectorAll('.notification-row')).toHaveLength(101));
    expect(document.querySelector('.notification-badge').textContent).toBe('99+');
  });

  it('refreshes after an SSE revision instead of applying stale row data', async () => {
    api.mockResolvedValueOnce({
      notifications: [{ id: 'n1', seq: 1, severity: 'error', title: '失败', message: 'boom', read_at: null }],
      unread_count: 1, latest_seq: 1, total_count: 1,
    }).mockResolvedValueOnce({
      notifications: [{ id: 'n1', seq: 1, severity: 'error', title: '失败', message: 'boom', read_at: 'now' }],
      unread_count: 0, latest_seq: 2, total_count: 1,
    });
    const { initNotificationCenter } = await import('../notification-center.js');
    initNotificationCenter();
    await vi.waitFor(() => expect(state.notificationUnread).toBe(1));

    eventSource.onmessage({ data: JSON.stringify({ seq: 2, refresh: true }) });

    await vi.waitFor(() => expect(state.notificationUnread).toBe(0));
    expect(state.notifications[0].read_at).toBe('now');
  });

  it('includes the canonical project directory in UI dedupe keys', async () => {
    state.currentProjectName = '同名项目';
    state.currentProjectDir = 'C:/projects/a';
    api.mockResolvedValueOnce({
      notification: { id: 'n2', seq: 5, severity: 'success', title: '通知', message: '完成', read_at: null },
      unread_count: 1,
    });
    const { registerNotification } = await import('../notification-center.js');

    await registerNotification({ message: '完成', severity: 'success', sourceType: 'ui_toast' });

    expect(api.mock.calls[0][1]).toContain('project_dir=C%3A%2Fprojects%2Fa');
    expect(api.mock.calls[0][2].dedupe_key).toContain('C:/projects/a');
  });

  it('does not let an older in-flight snapshot overwrite a mutation', async () => {
    api.mockResolvedValueOnce({
      notifications: [{ id: 'n1', seq: 1, severity: 'error', title: '失败', message: 'boom', read_at: null }],
      unread_count: 1, latest_seq: 1, total_count: 1,
    });
    const { initNotificationCenter, markNotificationRead, loadNotifications } = await import('../notification-center.js');
    initNotificationCenter();
    await vi.waitFor(() => expect(state.notificationUnread).toBe(1));

    let resolveSnapshot;
    api.mockImplementationOnce(() => new Promise(resolve => { resolveSnapshot = resolve; }));
    const loading = loadNotifications();
    await vi.waitFor(() => expect(resolveSnapshot).toBeTypeOf('function'));
    api.mockResolvedValueOnce({
      notification: { id: 'n1', seq: 1, severity: 'error', title: '失败', message: 'boom', read_at: 'now' },
      unread_count: 0, latest_seq: 2,
    });
    await markNotificationRead('n1');
    resolveSnapshot({
      notifications: [{ id: 'n1', seq: 1, severity: 'error', title: '失败', message: 'boom', read_at: null }],
      unread_count: 1, latest_seq: 1, total_count: 1,
    });
    await loading;

    expect(state.notificationUnread).toBe(0);
    expect(state.notifications[0].read_at).toBe('now');
  });
});
