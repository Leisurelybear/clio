import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({ api: vi.fn() }));
vi.mock('../toast.js', () => ({ addToast: vi.fn() }));

import { api } from '../api.js';
import { addToast } from '../toast.js';
import { state } from '../state.js';
import {
  fetchTask,
  filterTasks,
  loadTasks,
  renderTasks,
  selectTask,
  statusGroup,
  subscribeTask,
  subscribeTaskEvents,
  waitForTask,
} from '../task-center.js';

let source;
// Module-level _streamCursor persists across tests; stride keeps every new
// sequence above the largest cursor any earlier test could have advanced to.
let seq = 10_000_000;

function nextSeq() {
  seq += 10_000;
  return seq;
}

class FakeEventSource {
  constructor(url) {
    this.url = url;
    source = this;
  }
  close() {}
}

function message(payload) {
  source.onmessage({ data: JSON.stringify(payload) });
}

function pane() {
  document.body.innerHTML = '<div id="tab-tasks"></div><span id="status"></span>';
  return document.getElementById('tab-tasks');
}

describe('task center actions and stream', () => {
  beforeEach(() => {
    globalThis.EventSource = FakeEventSource;
    sessionStorage.clear();
    api.mockReset();
    addToast.mockReset();
    state.tasks = [];
    state.taskFilters = { status: 'all', kind: 'all', project: 'all' };
    state.selectedTaskId = null;
    state.taskDetail = null;
    state.taskLatestSeq = 0;
    state.currentEntity = 'tasks';
    pane();
  });

  it('does not crash when EventSource cannot be constructed', () => {
    globalThis.EventSource = class { constructor() { throw new Error('no sse'); } };
    const stop = subscribeTaskEvents(() => {});
    expect(typeof stop).toBe('function');
    stop();
  });

  it('opens the SSE stream once with the session token and isolates bad events', () => {
    sessionStorage.setItem('api_token', 'secret token');
    const seen = [];
    const stop = subscribeTaskEvents(payload => seen.push(payload));

    expect(source.url).toBe('/api/tasks/stream?after=0&token=secret%20token');
    const stop2 = subscribeTaskEvents(() => {});
    expect(source.url).toContain('after=0');

    source.onmessage({ data: '{broken' });
    expect(seen).toHaveLength(0);

    message({ seq: nextSeq(), task: { id: 't1', status: 'running' }, event: { seq: 1 } });
    expect(seen).toHaveLength(1);
    expect(state.tasks.map(task => task.id)).toEqual(['t1']);

    message({ seq: 1, task: { id: 't1', status: 'running' }, event: { seq: 1 } });
    expect(seen).toHaveLength(1);

    const failing = subscribeTaskEvents(() => {
      throw new Error('boom');
    });
    expect(() => message({ seq: nextSeq(), task: { id: 't2', status: 'running' }, event: { seq: 2 } })).not.toThrow();
    expect(seen).toHaveLength(2);

    message({ seq: nextSeq(), task: {}, event: { seq: 3 } });

    failing();
    stop2();
    stop();
  });

  it('ignores a non-function listener', () => {
    const stop = subscribeTaskEvents('nope');
    expect(typeof stop).toBe('function');
    stop();
  });

  it('keeps the task list sorted and bounded at 200 entries', () => {
    subscribeTaskEvents(() => {});
    state.tasks = Array.from({ length: 200 }, (_, i) => ({ id: `old${i}`, status: 'succeeded' }));

    message({ seq: nextSeq(), task: { id: 'fresh', status: 'running', updated_at: '2099-01-01' }, event: { seq: 1 } });

    expect(state.tasks).toHaveLength(200);
    expect(state.tasks[0].id).toBe('fresh');
  });

  it('classifies unknown statuses and unnamed projects', () => {
    expect(statusGroup('mystery')).toBe('mystery');
    expect(statusGroup()).toBe('unknown');
    expect(filterTasks([{ id: 'x', status: 'running' }], { status: 'all', kind: 'all', project: '未命名' }))
      .toHaveLength(1);
  });

  it('wires toolbar filters, refresh, row selection, and detail actions', async () => {
    const running = {
      id: 'a', status: 'running', kind: 'pipeline', project_name: 'Paris', title: 'A',
      cancellable: true, created_at: 'not-a-date',
    };
    const failed = {
      id: 'b', status: 'failed', kind: 'rerun', project_name: 'Tokyo', title: 'B',
      created_at: '2024-01-02', error_message: 'boom',
    };
    state.tasks = [running, failed];
    api.mockImplementation(async (method, url) => {
      if (url.startsWith('/api/tasks?')) return { tasks: [running, failed], latest_seq: 1 };
      if (url.includes('/cancel')) return { task: { ...running, status: 'cancelling' } };
      return { task: running, events: [{ seq: 1, level: 'warning', message: 'step', created_at: '2024-01-01' }] };
    });
    const el = document.getElementById('tab-tasks');

    renderTasks();

    el.querySelector('.task-filter-status').onchange({ target: { value: 'failed' } });
    expect(state.taskFilters.status).toBe('failed');
    el.querySelector('.task-filter-kind').onchange({ target: { value: 'rerun' } });
    expect(state.taskFilters.kind).toBe('rerun');
    el.querySelector('.task-filter-project').onchange({ target: { value: 'Tokyo' } });
    expect(state.taskFilters.project).toBe('Tokyo');
    el.querySelector('.task-refresh').onclick();

    state.taskFilters = { status: 'all', kind: 'all', project: 'all' };
    renderTasks();
    el.querySelector('.task-row').onclick();
    await vi.waitFor(() => expect(state.taskDetail?.task?.id).toBe('a'));
    expect(el.textContent).toContain('step');

    el.querySelector('.task-cancel').click();
    await vi.waitFor(() => expect(addToast).toHaveBeenCalledWith('已发送取消请求', 'success'));
  });

  it('retries a failed task from the detail pane', async () => {
    const failed = { id: 'b', status: 'failed', kind: 'rerun', title: 'B', created_at: '2024-01-02' };
    state.tasks = [failed];
    api.mockImplementation(async (method, url) => {
      if (url.startsWith('/api/tasks?')) return { tasks: [failed], latest_seq: 1 };
      if (url.includes('/retry')) return { task: { ...failed, id: 'b2', status: 'queued' } };
      return { task: failed, events: [] };
    });

    await loadTasks();
    await selectTask('b');
    document.querySelector('.task-retry').click();

    await vi.waitFor(() => expect(addToast).toHaveBeenCalledWith('已创建重试任务', 'success'));
    expect(state.selectedTaskId).toBe('b2');
  });

  it('reports a failed mutation', async () => {
    const running = { id: 'a', status: 'running', kind: 'pipeline', title: 'A', cancellable: true };
    state.tasks = [running];
    api.mockImplementation(async (method, url) => {
      if (url.startsWith('/api/tasks?')) return { tasks: [running], latest_seq: 1 };
      if (url.includes('/cancel')) throw new Error('nope');
      return { task: running, events: [] };
    });

    await loadTasks();
    await selectTask('a');
    document.querySelector('.task-cancel').click();

    await vi.waitFor(() => expect(addToast).toHaveBeenCalledWith('取消失败: nope', 'error', 6000));
  });

  it('merges live streamed tasks and prunes acknowledged sequences', async () => {
    subscribeTaskEvents(() => {});
    const liveSeq = nextSeq();
    message({ seq: liveSeq, task: { id: 'live', status: 'running', updated_at: '2099-01-01' }, event: { seq: 1 } });

    api.mockResolvedValue({ tasks: [{ id: 'snap', status: 'succeeded', updated_at: '2020-01-01' }], latest_seq: liveSeq - 1 });
    await loadTasks();
    expect(state.tasks.map(task => task.id)).toEqual(['live', 'snap']);

    api.mockResolvedValue({ tasks: [{ id: 'snap', status: 'succeeded', updated_at: '2020-01-01' }], latest_seq: liveSeq + 1 });
    await loadTasks();
    expect(state.tasks.map(task => task.id)).toEqual(['snap']);
  });

  it('drops a stale selection and reports load failures', async () => {
    state.selectedTaskId = 'gone';
    state.taskDetail = { task: { id: 'gone' }, events: [] };
    api.mockResolvedValue({ tasks: [{ id: 'snap', status: 'succeeded' }], latest_seq: 1 });

    await loadTasks();
    expect(state.selectedTaskId).toBeNull();
    expect(state.taskDetail).toBeNull();

    api.mockRejectedValue(new Error('down'));
    await loadTasks();
    expect(document.getElementById('status').textContent).toContain('任务加载失败');
  });

  it('ignores stale detail responses and reports detail failures', async () => {
    const deferred = [];
    api.mockImplementation(() => new Promise(resolve => deferred.push(resolve)));

    const first = selectTask('a');
    const second = selectTask('b');
    deferred[0]({ task: { id: 'a' }, events: [] });
    await first;
    expect(state.taskDetail).toBeNull();
    deferred[1]({ task: { id: 'b' }, events: [] });
    await second;
    expect(state.taskDetail.task.id).toBe('b');

    api.mockRejectedValue(new Error('nope'));
    await selectTask('c');
    expect(document.getElementById('status').textContent).toContain('任务详情加载失败');
  });

  it('fetches a single task snapshot and handles missing ids', async () => {
    api.mockResolvedValue({ task: { id: 'x' } });
    await expect(fetchTask('x')).resolves.toEqual({ id: 'x' });
    expect(await fetchTask('')).toBeNull();
    api.mockResolvedValue({});
    expect(await fetchTask('y')).toBeNull();
  });

  it('buffers live events until the snapshot arrives', async () => {
    let resolveApi;
    api.mockImplementation(() => new Promise(resolve => {
      resolveApi = resolve;
    }));
    const listener = vi.fn();
    const pending = subscribeTask('t1', listener);

    message({ seq: nextSeq(), task: { id: 't1', status: 'running' }, event: { seq: 1 } });
    expect(listener).not.toHaveBeenCalled();

    resolveApi({ task: { id: 't1', status: 'running' }, events: [{ seq: 0 }] });
    const stop = await pending;
    expect(listener).toHaveBeenCalledTimes(2);
    expect(listener.mock.calls[0][0].snapshot).toBe(true);
    stop();
  });

  it('returns a no-op unsubscribe for invalid subscription arguments', async () => {
    expect(typeof (await subscribeTask('', () => {}))).toBe('function');
    expect(typeof (await subscribeTask('t1', 'nope'))).toBe('function');
  });

  it('rejects subscribeTask when the snapshot request fails', async () => {
    api.mockRejectedValue(new Error('offline'));
    await expect(subscribeTask('t1', () => {})).rejects.toThrow('offline');
  });

  it('resolves waitForTask on success and rejects with a task-backed error', async () => {
    api.mockResolvedValue({ task: { id: 'ok', status: 'succeeded' }, events: [] });
    await expect(waitForTask('ok')).resolves.toMatchObject({ id: 'ok' });

    api.mockResolvedValue({ task: { id: 'bad', status: 'failed', error_message: 'exploded' }, events: [] });
    await expect(waitForTask('bad')).rejects.toMatchObject({ taskBacked: true, taskId: 'bad', message: 'exploded' });
  });

  it('rejects waitForTask when the snapshot request fails', async () => {
    api.mockRejectedValue(new Error('offline'));
    await expect(waitForTask('x')).rejects.toThrow('offline');
  });

  it('renders nothing without a task pane', () => {
    document.body.innerHTML = '';
    api.mockResolvedValue({ tasks: [], latest_seq: 0 });

    expect(() => renderTasks()).not.toThrow();
  });

  it('filters out tasks whose project does not match', () => {
    const tasks = [
      { id: 'a', status: 'running', project_name: 'Paris' },
      { id: 'b', status: 'running', project_name: 'Tokyo' },
      { id: 'c', status: 'running' },
    ];

    expect(filterTasks(tasks, { status: 'all', kind: 'all', project: 'Paris' }).map(task => task.id))
      .toEqual(['a']);
    expect(filterTasks(tasks, { status: 'all', kind: 'all', project: '未命名' }).map(task => task.id))
      .toEqual(['c']);
  });

  it('ignores a mutation when no task is selected', async () => {
    const running = { id: 'a', status: 'running', kind: 'pipeline', title: 'A', cancellable: true };
    state.tasks = [running];
    api.mockImplementation(async (method, url) => {
      if (url.startsWith('/api/tasks?')) return { tasks: [running], latest_seq: 1 };
      return { task: running, events: [] };
    });
    await loadTasks();
    await selectTask('a');
    const callsBefore = api.mock.calls.length;

    state.selectedTaskId = null;
    document.querySelector('.task-cancel').click();
    await Promise.resolve();

    expect(api.mock.calls.length).toBe(callsBefore);
    expect(addToast).not.toHaveBeenCalled();
  });

  it('appends streamed events to the open task detail once', async () => {
    const running = { id: 'a', status: 'running', kind: 'pipeline', title: 'A' };
    subscribeTaskEvents(() => {});
    api.mockResolvedValue({ task: running, events: [{ seq: 1, level: 'info', message: 'first' }] });
    await selectTask('a');
    expect(state.taskDetail.events).toHaveLength(1);

    message({ seq: nextSeq(), task: running, event: { seq: 2, level: 'info', message: 'second' } });
    expect(state.taskDetail.events.map(event => event.seq)).toEqual([1, 2]);

    message({ seq: nextSeq(), task: running, event: { seq: 2, level: 'info', message: 'dup' } });
    expect(state.taskDetail.events).toHaveLength(2);
  });

  it('ignores streamed events for other tasks during a subscription', async () => {
    api.mockResolvedValue({ task: { id: 't1', status: 'running' }, events: [] });
    const listener = vi.fn();
    const stop = await subscribeTask('t1', listener);
    expect(listener).toHaveBeenCalledTimes(1);

    message({ seq: nextSeq(), task: { id: 'other', status: 'running' }, event: { seq: 1 } });
    expect(listener).toHaveBeenCalledTimes(1);
    stop();
  });

  it('waits through non-terminal updates before resolving', async () => {
    api.mockResolvedValue({ task: { id: 't1', status: 'running' }, events: [] });
    const pending = waitForTask('t1');

    message({ seq: nextSeq(), task: { id: 't1', status: 'running' }, event: { seq: 1 } });
    message({ seq: nextSeq(), task: { id: 't1', status: 'succeeded' }, event: { seq: 2 } });

    await expect(pending).resolves.toMatchObject({ id: 't1', status: 'succeeded' });
  });

  it('caps the open task detail at 300 events', async () => {
    const running = { id: 'a', status: 'running', kind: 'pipeline', title: 'A' };
    const events = Array.from({ length: 300 }, (_, index) => ({ seq: index + 1, message: `e${index}` }));
    api.mockResolvedValue({ task: running, events });
    subscribeTaskEvents(() => {});
    await selectTask('a');
    expect(state.taskDetail.events).toHaveLength(300);

    message({ seq: nextSeq(), task: running, event: { seq: 301, message: 'new' } });

    expect(state.taskDetail.events).toHaveLength(300);
    expect(state.taskDetail.events.at(-1).seq).toBe(301);
  });
});
