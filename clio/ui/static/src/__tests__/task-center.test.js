import { describe, expect, it } from 'vitest';
import { filterTasks, isTaskRetryable, kindLabel, statusGroup, statusLabel } from '../task-center.js';

const tasks = [
  { id: 'a', status: 'running', kind: 'pipeline', project_name: 'Paris' },
  { id: 'b', status: 'failed', kind: 'rerun', project_name: 'Paris' },
  { id: 'c', status: 'succeeded', kind: 'cut_export', project_name: 'Tokyo' },
];

describe('task center helpers', () => {
  it('maps labels and status groups', () => {
    expect(statusLabel('running')).toBe('运行中');
    expect(kindLabel('cut_export')).toBe('裁剪导出');
    expect(statusGroup('queued')).toBe('active');
    expect(statusGroup('failed')).toBe('failed');
    expect(statusGroup('succeeded')).toBe('done');
  });

  it('filters by status group, kind, and project', () => {
    expect(filterTasks(tasks, { status: 'active', kind: 'all', project: 'all' }).map(t => t.id)).toEqual(['a']);
    expect(filterTasks(tasks, { status: 'all', kind: 'rerun', project: 'Paris' }).map(t => t.id)).toEqual(['b']);
    expect(filterTasks(tasks, { status: 'done', kind: 'all', project: 'Tokyo' }).map(t => t.id)).toEqual(['c']);
  });

  it('allows retry only for terminal tasks with retryable inputs', () => {
    expect(isTaskRetryable({ status: 'failed' })).toBe(true);
    expect(isTaskRetryable({ status: 'cancelled' })).toBe(true);
    expect(isTaskRetryable({ status: 'running' })).toBe(false);
    expect(isTaskRetryable({ status: 'failed', input_summary: { retryable: false } })).toBe(false);
  });
});

import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({ api: vi.fn() }));

import { api } from '../api.js';
import { state } from '../state.js';

class FakeEventSource {
  constructor(url) { this.url = url; }
  close() {}
}

describe('task nav badge', () => {
  beforeEach(() => {
    globalThis.EventSource = FakeEventSource;
    sessionStorage.clear();
    api.mockReset();
    state.tasks = [];
    state.taskLatestSeq = 0;
    document.body.innerHTML = '<div id="tab-tasks"></div><li class="project-item" data-entity="tasks"><span class="task-nav-badge" hidden>0</span></li>';
  });

  it('shows active task count on the nav badge after loadTasks', async () => {
    api.mockResolvedValue({
      tasks: [
        { id: 'a', status: 'running', kind: 'pipeline', updated_at: '2024-01-01' },
        { id: 'b', status: 'queued', kind: 'rerun', updated_at: '2024-01-02' },
        { id: 'c', status: 'succeeded', kind: 'pipeline', updated_at: '2024-01-03' },
      ],
      latest_seq: 10,
    });
    const { loadTasks } = await import('../task-center.js');
    await loadTasks();
    const badge = document.querySelector('.task-nav-badge');
    expect(badge.hidden).toBe(false);
    expect(badge.textContent).toBe('2');
  });

  it('hides the badge when there are no active tasks', async () => {
    api.mockResolvedValue({
      tasks: [{ id: 'x', status: 'succeeded', kind: 'pipeline', updated_at: '2024-01-01' }],
      latest_seq: 1,
    });
    const { loadTasks } = await import('../task-center.js');
    await loadTasks();
    const badge = document.querySelector('.task-nav-badge');
    expect(badge.hidden).toBe(true);
  });

  it('hides retry and explains why a task cannot be retried', async () => {
    const task = {
      id: 'blocked',
      status: 'failed',
      kind: 'pipeline',
      title: '处理素材',
      input_summary: {
        retryable: false,
        retry_blocked_reason: '此任务使用了临时上下文或自定义提示词，无法安全重试；请重新提交运行。',
      },
      updated_at: '2024-01-01',
    };
    api.mockResolvedValueOnce({ tasks: [task], latest_seq: 1 });
    const { loadTasks, selectTask } = await import('../task-center.js');
    await loadTasks();
    api.mockResolvedValueOnce({ task, events: [], latest_seq: 1 });

    await selectTask(task.id);

    expect(document.querySelector('.task-retry')).toBeNull();
    expect(document.querySelector('#tab-tasks').textContent).toContain('无法安全重试');
  });
});
