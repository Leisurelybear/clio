import { describe, expect, it } from 'vitest';
import { filterTasks, kindLabel, statusGroup, statusLabel } from '../task-center.js';

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
});
