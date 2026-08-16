import { api } from './api.js';
import { waitForTask } from './task-center.js';

export async function exportJianyingDraft(day, { force = false } = {}) {
  const response = await api('POST', '/api/export', {
    day: day || 'day1',
    format: 'jianying',
    force: Boolean(force),
  });
  const task = response?.task_id ? await waitForTask(response.task_id) : null;
  return {
    task,
    artifact: task?.result_summary?.artifact || response?.artifact || response?.path || '',
  };
}
