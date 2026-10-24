import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({ api: vi.fn() }));
vi.mock('../sidebar.js', () => ({ selectTasks: vi.fn().mockResolvedValue(undefined) }));
vi.mock('../task-center.js', () => ({ selectTask: vi.fn().mockResolvedValue(undefined) }));

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
    const { seedCatalogs } = await import('./catalogs.js');
    seedCatalogs();
    ({ state } = await import('../state.js'));
    ({ api } = await import('../api.js'));
    api.mockReset();
    vi.clearAllMocks();
    state.notifications = [];
    state.notificationUnread = 0;
    state.notificationLatestSeq = 0;
    document.body.innerHTML = `<span id="status"></span>
      <button id="btn-notifications"><span class="notification-badge" hidden></span></button>
      <section id="notification-panel" hidden></section>`;
    globalThis.EventSource = FakeEventSource;
    eventSource = null;
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

  it('localizes the status notification title in the active locale', async () => {
    api.mockResolvedValue({});
    const { applyLocale } = await import('../i18n.js');
    const { setStatus } = await import('../utils.js');
    applyLocale('en');

    await setStatus('预览播放完毕', 'ok');

    expect(api).toHaveBeenCalledWith('POST', '/api/notifications', expect.objectContaining({
      title: 'Operation complete', severity: 'success', source_type: 'ui_status',
    }));
    applyLocale('zh-CN');
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

  it('does not dispatch creation events for the initial history load', async () => {
    api.mockResolvedValueOnce({
      notifications: [{ id: 'n1', seq: 1, severity: 'success', title: '完成', message: '旧记录', read_at: null }],
      unread_count: 1, latest_seq: 1, total_count: 1,
    });
    const created = vi.fn();
    window.addEventListener('clio:notification-created', created);
    const { initNotificationCenter } = await import('../notification-center.js');

    initNotificationCenter();
    await vi.waitFor(() => expect(state.notifications).toHaveLength(1));

    expect(created).not.toHaveBeenCalled();
    window.removeEventListener('clio:notification-created', created);
  });

  it('dispatches each notification created by an SSE refresh exactly once', async () => {
    api.mockResolvedValueOnce({
      notifications: [{ id: 'n1', seq: 1, severity: 'success', title: '完成', message: '旧记录', read_at: null }],
      unread_count: 1, latest_seq: 1, total_count: 1,
    }).mockResolvedValueOnce({
      notifications: [
        { id: 'n2', seq: 2, severity: 'success', title: '完成', message: '新记录', read_at: null },
        { id: 'n1', seq: 1, severity: 'success', title: '完成', message: '旧记录', read_at: null },
      ],
      unread_count: 2, latest_seq: 2, total_count: 2,
    }).mockResolvedValueOnce({
      notifications: [
        { id: 'n2', seq: 2, severity: 'success', title: '完成', message: '新记录', read_at: null },
        { id: 'n1', seq: 1, severity: 'success', title: '完成', message: '旧记录', read_at: null },
      ],
      unread_count: 2, latest_seq: 3, total_count: 2,
    });
    const created = vi.fn();
    window.addEventListener('clio:notification-created', created);
    const { initNotificationCenter } = await import('../notification-center.js');
    initNotificationCenter();
    await vi.waitFor(() => expect(state.notifications).toHaveLength(1));

    eventSource.onmessage({ data: JSON.stringify({ seq: 2, refresh: true }) });
    await vi.waitFor(() => expect(state.notifications).toHaveLength(2));
    await vi.waitFor(() => expect(created).toHaveBeenCalledTimes(1));
    expect(created.mock.calls[0][0].detail.id).toBe('n2');

    eventSource.onmessage({ data: JSON.stringify({ seq: 3, refresh: true }) });
    await vi.waitFor(() => expect(state.notificationLatestSeq).toBe(3));
    await vi.waitFor(() => expect(api).toHaveBeenCalledTimes(3));
    expect(created).toHaveBeenCalledTimes(1);
    window.removeEventListener('clio:notification-created', created);
  });

  it('preserves loaded pages when an SSE refresh reloads the newest page', async () => {
    const firstPage = Array.from({ length: 100 }, (_, index) => ({
      id: `n${index}`, seq: 100 - index, severity: 'warning', title: '提醒', message: `m${index}`, read_at: null,
    }));
    let firstPageLoads = 0;
    api.mockImplementation(async (method, url) => {
      if (method !== 'GET') return {};
      const params = new URL(url, 'http://127.0.0.1').searchParams;
      if (params.get('offset') === '100') {
        return { notifications: [{ id: 'n-old', seq: 0, severity: 'warning', title: '旧', message: 'old', read_at: null }], unread_count: 101, latest_seq: 1, total_count: 101 };
      }
      firstPageLoads += 1;
      return firstPageLoads === 1
        ? { notifications: firstPage, unread_count: 101, latest_seq: 1, total_count: 101 }
        : {
          notifications: [{ id: 'n-new', seq: 101, severity: 'warning', title: '新', message: 'new', read_at: null }, ...firstPage],
          unread_count: 102, latest_seq: 2, total_count: 102,
        };
    });
    const { initNotificationCenter, loadNotifications } = await import('../notification-center.js');
    initNotificationCenter();
    await vi.waitFor(() => expect(state.notifications).toHaveLength(100));
    await loadNotifications({ append: true });
    expect(state.notifications).toHaveLength(101);

    eventSource.onmessage({ data: JSON.stringify({ seq: 2, refresh: true }) });

    await vi.waitFor(() => expect(state.notifications).toHaveLength(102));
    expect(state.notifications.some(item => item.id === 'n-old')).toBe(true);
    expect(api).toHaveBeenCalledWith('GET', expect.stringContaining('limit=101&offset=0'));
  });

  it('advances the offset by returned rows when append dedupes an older page row', async () => {
    const firstPage = Array.from({ length: 100 }, (_, index) => ({
      id: `n${index}`, seq: 102 - index, severity: 'warning', title: '提醒', message: `m${index}`, read_at: null,
    }));
    api.mockImplementation(async (method, url) => {
      if (method !== 'GET') return {};
      const offset = new URL(url, 'http://127.0.0.1').searchParams.get('offset');
      if (offset === '100') {
        return {
          notifications: [
            { id: 'n0', seq: 102, severity: 'warning', title: '提醒', message: 'duplicate', read_at: null },
            { id: 'n100', seq: 2, severity: 'warning', title: '提醒', message: 'new row', read_at: null },
          ],
          unread_count: 102, latest_seq: 1, total_count: 102,
        };
      }
      if (offset === '102') {
        return {
          notifications: [{ id: 'n101', seq: 1, severity: 'warning', title: '提醒', message: 'last row', read_at: null }],
          unread_count: 102, latest_seq: 1, total_count: 102,
        };
      }
      return { notifications: firstPage, unread_count: 102, latest_seq: 1, total_count: 102 };
    });
    const { initNotificationCenter, loadNotifications } = await import('../notification-center.js');
    initNotificationCenter();
    await vi.waitFor(() => expect(state.notifications).toHaveLength(100));

    await loadNotifications({ append: true });
    expect(state.notifications).toHaveLength(101);
    await loadNotifications({ append: true });

    expect(api.mock.calls.at(-1)[1]).toContain('offset=102');
    expect(state.notifications).toHaveLength(102);
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

  it('coalesces a burst of SSE revisions into one refresh request', async () => {
    api.mockResolvedValueOnce({
      notifications: [{ id: 'n1', seq: 1, severity: 'error', title: '失败', message: 'boom', read_at: null }],
      unread_count: 1, latest_seq: 1, total_count: 1,
    }).mockResolvedValue({
      notifications: [
        { id: 'n3', seq: 3, severity: 'info', title: 'T', message: 'm', read_at: null },
        { id: 'n2', seq: 2, severity: 'info', title: 'T', message: 'm', read_at: null },
        { id: 'n1', seq: 1, severity: 'error', title: '失败', message: 'boom', read_at: null },
      ],
      unread_count: 3, latest_seq: 3, total_count: 3,
    });
    const { initNotificationCenter } = await import('../notification-center.js');
    initNotificationCenter();
    await vi.waitFor(() => expect(eventSource).toBeTruthy());
    const getCalls = () => api.mock.calls.filter(([method]) => method === 'GET').length;
    const before = getCalls();

    eventSource.onmessage({ data: JSON.stringify({ seq: 2, refresh: true }) });
    eventSource.onmessage({ data: JSON.stringify({ seq: 3, refresh: true }) });

    await vi.waitFor(() => expect(state.notifications).toHaveLength(3));
    expect(getCalls()).toBe(before + 1);
    await new Promise(resolve => setTimeout(resolve, 250));
    expect(getCalls()).toBe(before + 1);
  });

  it('keeps a debounced SSE refresh when load-more starts first', async () => {
    const n1 = { id: 'n1', seq: 1, severity: 'error', title: '失败', message: 'boom', read_at: null };
    const n2 = { id: 'n2', seq: 2, severity: 'info', title: 'T', message: 'm', read_at: null };
    api.mockResolvedValueOnce({
      notifications: [n1], unread_count: 1, latest_seq: 1, total_count: 1,
    });
    const { initNotificationCenter, loadNotifications } = await import('../notification-center.js');
    initNotificationCenter();
    await vi.waitFor(() => expect(eventSource).toBeTruthy());
    api.mockImplementation((method, url) => {
      if (method !== 'GET') return Promise.resolve({});
      return Promise.resolve(url.includes('offset=0')
        ? { notifications: [n2, n1], unread_count: 2, latest_seq: 2, total_count: 2 }
        : { notifications: [n1], unread_count: 2, latest_seq: 2, total_count: 2 });
    });

    eventSource.onmessage({ data: JSON.stringify({ seq: 2, refresh: true }) });
    await loadNotifications({ append: true });

    await vi.waitFor(() => expect(state.notifications.map(item => item.id)).toEqual(['n2', 'n1']));
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

  it('renders a per-notification read button for unread items', async () => {
    api.mockResolvedValue({
      notifications: [{ id: 'n1', seq: 1, severity: 'warning', title: '提醒', message: '检查', read_at: null }],
      unread_count: 1, latest_seq: 1, total_count: 1,
    });
    const { initNotificationCenter, markNotificationRead } = await import('../notification-center.js');
    initNotificationCenter();
    await vi.waitFor(() => expect(state.notificationUnread).toBe(1));
    document.getElementById('btn-notifications').click();
    await vi.waitFor(() => expect(document.querySelector('.notification-read-one')).toBeTruthy());

    const readBtn = document.querySelector('.notification-read-one');
    expect(readBtn.dataset.readId).toBe('n1');

    api.mockResolvedValueOnce({
      notification: { id: 'n1', seq: 1, severity: 'warning', title: '提醒', message: '检查', read_at: 'now' },
      unread_count: 0, latest_seq: 2,
    });
    readBtn.click();
    await vi.waitFor(() => expect(state.notificationUnread).toBe(0));
    expect(document.querySelector('.notification-read-one')).toBeNull();
  });

  it('does not show a read button for already-read notifications', async () => {
    api.mockResolvedValue({
      notifications: [{ id: 'n2', seq: 2, severity: 'info', title: '旧消息', message: '已读', read_at: '2024-01-01' }],
      unread_count: 0, latest_seq: 2, total_count: 1,
    });
    const { initNotificationCenter } = await import('../notification-center.js');
    initNotificationCenter();
    await vi.waitFor(() => expect(state.notifications).toHaveLength(1));
    document.getElementById('btn-notifications').click();
    await vi.waitFor(() => expect(document.querySelector('.notification-row')).toBeTruthy());
    expect(document.querySelector('.notification-read-one')).toBeNull();
  });

  it('marks every notification read from the panel action', async () => {
    api.mockResolvedValue({
      notifications: [{ id: 'n1', seq: 1, severity: 'warning', title: '提醒', message: 'm', read_at: null }],
      unread_count: 1, latest_seq: 1, total_count: 1,
    });
    const { initNotificationCenter } = await import('../notification-center.js');
    initNotificationCenter();
    await vi.waitFor(() => expect(state.notificationUnread).toBe(1));
    document.getElementById('btn-notifications').click();
    await vi.waitFor(() => expect(document.querySelector('.notification-read-all')).toBeTruthy());
    document.querySelector('[data-filter="unread"]').click();
    await vi.waitFor(() => expect(state.notifications).toHaveLength(1));

    api.mockResolvedValueOnce({ marked: 1, unread_count: 0, latest_seq: 2 });
    document.querySelector('.notification-read-all').click();
    await vi.waitFor(() => expect(state.notificationUnread).toBe(0));

    expect(state.notifications).toHaveLength(0);
  });

  it('routes an opened notification to its action handler', async () => {
    const { openNotification } = await import('../notification-center.js');
    const seen = [];
    window.addEventListener('clio:notification-action', event => seen.push(event.detail));
    state.notifications = [{ id: 'a', seq: 3, data: { action_id: 'act-1' }, read_at: null }];
    api.mockResolvedValue({ notification: { id: 'a', seq: 3, read_at: 'now' }, latest_seq: 4 });

    await openNotification('a');
    expect(seen[0].id).toBe('act-1');

    await openNotification('missing');
    expect(api).toHaveBeenCalledTimes(1);
  });

  it('routes an opened notification to its task', async () => {
    const sidebar = await import('../sidebar.js');
    const tasks = await import('../task-center.js');
    const { openNotification } = await import('../notification-center.js');
    state.notifications = [{ id: 'b', seq: 2, task_id: 'task-9', read_at: null }];
    state.currentEntity = 'tasks';
    api.mockResolvedValue({ notification: { id: 'b', seq: 2, read_at: 'now' }, latest_seq: 3 });

    await openNotification('b');

    expect(sidebar.selectTasks).toHaveBeenCalled();
    expect(tasks.selectTask).toHaveBeenCalledWith('task-9');
  });

  it('stops a task deep link when the tasks view cannot open', async () => {
    const sidebar = await import('../sidebar.js');
    const tasks = await import('../task-center.js');
    const { openNotification } = await import('../notification-center.js');
    state.notifications = [{ id: 'd', seq: 1, task_id: 'task-1', read_at: null }];
    state.currentEntity = 'videos';
    api.mockResolvedValue({ notification: { id: 'd', seq: 1, read_at: 'now' }, latest_seq: 2 });

    await openNotification('d');

    expect(sidebar.selectTasks).toHaveBeenCalled();
    expect(tasks.selectTask).not.toHaveBeenCalled();
  });

  it('navigates to a notification link as a last resort', async () => {
    const { openNotification } = await import('../notification-center.js');
    state.notifications = [{ id: 'c', seq: 1, link: '#somewhere', read_at: null }];
    api.mockResolvedValue({ notification: { id: 'c', seq: 1, read_at: 'now' }, latest_seq: 2 });

    await expect(openNotification('c')).resolves.toBeUndefined();
  });

  it('reconnects the SSE stream after an error', async () => {
    api.mockResolvedValue({ notifications: [], unread_count: 0, latest_seq: 0, total_count: 0 });
    const { initNotificationCenter } = await import('../notification-center.js');
    initNotificationCenter();
    await vi.waitFor(() => expect(eventSource).toBeTruthy());
    const first = eventSource;

    vi.useFakeTimers();
    try {
      first.onerror();
      await vi.advanceTimersByTimeAsync(3_100);
    } finally {
      vi.useRealTimers();
    }

    expect(eventSource).not.toBe(first);
  });

  it('keeps only warnings and errors in the attention filter', async () => {
    api.mockResolvedValue({
      notifications: [
        { id: 'a', seq: 3, severity: 'error', title: 'E', message: 'm', read_at: null, created_at: 'not-a-date' },
        { id: 'b', seq: 2, severity: 'info', title: 'I', message: 'm', read_at: null },
      ],
      unread_count: 2, latest_seq: 3, total_count: 2,
    });
    const { initNotificationCenter } = await import('../notification-center.js');
    initNotificationCenter();
    await vi.waitFor(() => expect(state.notifications).toHaveLength(2));
    document.getElementById('btn-notifications').click();
    await vi.waitFor(() => expect(document.querySelector('[data-filter="attention"]')).toBeTruthy());

    document.querySelector('[data-filter="attention"]').click();
    await vi.waitFor(() => expect(state.notifications).toHaveLength(1));

    expect(state.notifications[0].severity).toBe('error');
  });

  it('streams individual notifications and ignores stale revisions', async () => {
    sessionStorage.setItem('api_token', 'sekret');
    api.mockResolvedValue({ notifications: [], unread_count: 0, latest_seq: 3, total_count: 0 });
    const { initNotificationCenter } = await import('../notification-center.js');
    initNotificationCenter();
    await vi.waitFor(() => expect(eventSource).toBeTruthy());
    expect(eventSource.url).toContain('token=sekret');

    eventSource.onmessage({ data: JSON.stringify({ seq: 2, notification: { id: 'old', seq: 2, message: 'm' } }) });
    expect(state.notifications).toHaveLength(0);

    eventSource.onmessage({ data: JSON.stringify({ seq: 4, notification: { seq: 4 } }) });
    expect(state.notifications).toHaveLength(0);

    eventSource.onmessage({ data: JSON.stringify({
      seq: 5,
      notification: { id: 'new', seq: 5, severity: 'info', title: 'T', message: 'm', read_at: null },
    }) });
    expect(state.notifications.map(item => item.id)).toEqual(['new']);
    expect(state.notificationUnread).toBe(1);
  });

  it('drops an upserted notification that no longer matches the filter', async () => {
    api.mockResolvedValue({
      notifications: [{ id: 'n1', seq: 1, severity: 'warning', title: 'W', message: 'm', read_at: null }],
      unread_count: 1, latest_seq: 1, total_count: 1,
    });
    const { initNotificationCenter } = await import('../notification-center.js');
    initNotificationCenter();
    await vi.waitFor(() => expect(state.notificationUnread).toBe(1));
    document.getElementById('btn-notifications').click();
    await vi.waitFor(() => expect(document.querySelector('[data-filter="attention"]')).toBeTruthy());
    document.querySelector('[data-filter="attention"]').click();
    await vi.waitFor(() => expect(state.notifications).toHaveLength(1));

    eventSource.onmessage({ data: JSON.stringify({
      seq: 2,
      notification: { id: 'n1', seq: 2, severity: 'info', title: 'W', message: 'm', read_at: null },
    }) });

    expect(state.notifications).toHaveLength(0);
    expect(state.notificationUnread).toBe(0);
  });

  it('adjusts the unread count when an upsert changes read state', async () => {
    api.mockResolvedValue({
      notifications: [{ id: 'n1', seq: 1, severity: 'warning', title: 'W', message: 'm', read_at: null }],
      unread_count: 1, latest_seq: 1, total_count: 1,
    });
    const { initNotificationCenter } = await import('../notification-center.js');
    initNotificationCenter();
    await vi.waitFor(() => expect(state.notificationUnread).toBe(1));

    eventSource.onmessage({ data: JSON.stringify({
      seq: 2,
      notification: { id: 'n1', seq: 2, severity: 'warning', title: 'W', message: 'm', read_at: 'now' },
    }) });

    expect(state.notificationUnread).toBe(0);
    expect(state.notifications[0].read_at).toBe('now');
  });

  it('hashes an over-long dedupe key', async () => {
    api.mockResolvedValueOnce({
      notification: { id: 'n9', seq: 6, severity: 'info', title: 'T', message: 'm', read_at: null },
      unread_count: 1,
    });
    const { registerNotification } = await import('../notification-center.js');

    await registerNotification({ message: 'm', dedupeKey: 'x'.repeat(400) });

    const sent = api.mock.calls[0][2].dedupe_key;
    expect(sent.length).toBeLessThan(320);
    expect(sent).toContain(':');
  });

  it('builds a context link from the current selection and rejects empty messages', async () => {
    state.currentEntity = 'videos';
    state.currentVideo = 'v1';
    state.currentDay = 'day1';
    state.currentProjectName = 'Trip';
    state.currentProjectDir = 'D:\\clips\\day1';
    api.mockResolvedValueOnce({
      notification: { id: 'n8', seq: 7, severity: 'info', title: 'T', message: 'm', read_at: null },
      unread_count: 1,
    });
    const { registerNotification, markNotificationRead } = await import('../notification-center.js');

    expect(await registerNotification({})).toBeNull();
    await registerNotification({ message: 'm' });
    const link = api.mock.calls[0][2].link;
    expect(link).toContain('video=v1');
    expect(link).toContain('entity=videos');
    expect(link).toContain('day=day1');
    // A Windows project_dir must stay percent-encoded: the backend accepts a
    // backslash inside the query but still rejects it in the path part.
    expect(link).toContain('project_dir=D%3A%5Cclips%5Cday1');

    await markNotificationRead('');
    expect(api).toHaveBeenCalledTimes(1);
  });

  it('tolerates a snapshot without a revision', async () => {
    api.mockResolvedValue({ notifications: [{ id: 'n1', seq: 1, severity: 'info', title: 'T', message: 'm', read_at: null }] });
    const { loadNotifications } = await import('../notification-center.js');

    await loadNotifications();

    expect(state.notifications).toHaveLength(1);
  });

  it('opens a notification when its row is clicked', async () => {
    api.mockResolvedValue({
      notifications: [{
        id: 'n1', seq: 1, severity: 'info', title: 'T', message: 'm', read_at: null, data: { action_id: 'a1' },
      }],
      unread_count: 1, latest_seq: 1, total_count: 1,
    });
    const { initNotificationCenter } = await import('../notification-center.js');
    const seen = [];
    window.addEventListener('clio:notification-action', event => seen.push(event.detail));
    initNotificationCenter();
    await vi.waitFor(() => expect(state.notificationUnread).toBe(1));
    document.getElementById('btn-notifications').click();
    await vi.waitFor(() => expect(document.querySelector('.notification-row')).toBeTruthy());

    document.querySelector('.notification-row').click();
    await vi.waitFor(() => expect(seen).toHaveLength(1));

    expect(seen[0].id).toBe('a1');
  });

  it('initializes only once', async () => {
    api.mockResolvedValue({ notifications: [], unread_count: 0, latest_seq: 0, total_count: 0 });
    const { initNotificationCenter } = await import('../notification-center.js');

    initNotificationCenter();
    initNotificationCenter();

    await vi.waitFor(() => expect(eventSource).toBeTruthy());
  });

  it('does nothing without the notification panel', async () => {
    document.body.innerHTML = '';
    const { initNotificationCenter } = await import('../notification-center.js');

    expect(() => initNotificationCenter()).not.toThrow();
  });
});
