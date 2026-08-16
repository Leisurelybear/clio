import { describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({ api: vi.fn() }));
vi.mock('../task-center.js', () => ({ waitForTask: vi.fn() }));

import { api } from '../api.js';
import { waitForTask } from '../task-center.js';
import { exportJianyingDraft } from '../plan-export.js';

describe('plan export service', () => {
  it('waits for the managed export task and returns its artifact id', async () => {
    api.mockResolvedValue({ task_id: 'task-1', artifact: 'pending' });
    waitForTask.mockResolvedValue({
      id: 'task-1',
      status: 'succeeded',
      result_summary: { artifact: 'export/day1_jianying' },
    });

    const result = await exportJianyingDraft('day1', { force: true });

    expect(api).toHaveBeenCalledWith('POST', '/api/export', {
      day: 'day1', format: 'jianying', force: true,
    });
    expect(waitForTask).toHaveBeenCalledWith('task-1');
    expect(result.artifact).toBe('export/day1_jianying');
  });
});
