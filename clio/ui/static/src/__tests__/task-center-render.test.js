import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({ api: vi.fn() }));

import { api } from '../api.js';
import { state } from '../state.js';
import { renderTasks, subscribeTaskEvents } from '../task-center.js';

let source;
let seq = 0;

class FakeEventSource {
  constructor(url) {
    this.url = url;
    source = this;
  }
  close() {}
}

function message(task) {
  seq += 1;
  source.onmessage({ data: JSON.stringify({ seq, task, event: { seq } }) });
}

describe('task center stream rendering', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('requestAnimationFrame', cb => setTimeout(cb, 16));
    globalThis.EventSource = FakeEventSource;
    sessionStorage.clear();
    api.mockReset();
    state.tasks = [];
    state.taskFilters = { status: 'all', kind: 'all', project: 'all' };
    state.selectedTaskId = null;
    state.taskDetail = null;
    state.currentEntity = 'tasks';
    document.body.innerHTML = '<div id="tab-tasks"></div><span id="status"></span>';
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('coalesces a burst of events into one render per frame', () => {
    const stop = subscribeTaskEvents(() => {});
    renderTasks();
    const pane = document.getElementById('tab-tasks');
    const toolbar = pane.querySelector('.task-center-toolbar');
    for (let i = 0; i < 5; i += 1) message({ id: `t${i}`, status: 'running' });
    expect(pane.querySelector('.task-center-toolbar')).toBe(toolbar);
    vi.advanceTimersByTime(20);
    expect(pane.querySelectorAll('.task-row')).toHaveLength(5);
    stop();
  });

  it('does not rebuild the toolbar while a filter select has focus', () => {
    const stop = subscribeTaskEvents(() => {});
    renderTasks();
    const pane = document.getElementById('tab-tasks');
    const select = pane.querySelector('.task-filter-kind');
    select.focus();
    message({ id: 'x1', status: 'running' });
    vi.advanceTimersByTime(20);
    expect(pane.querySelector('.task-filter-kind')).toBe(select);

    select.blur();
    vi.advanceTimersByTime(20);
    expect(pane.querySelector('.task-filter-kind')).not.toBe(select);
    expect(pane.querySelectorAll('.task-row').length).toBeGreaterThan(0);
    stop();
  });
});
