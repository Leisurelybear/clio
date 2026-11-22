// Archived pre-fix probes. Superseded by src/__tests__/runner-recovery.test.js.
// Passing assertions describe the original review baseline, not current behavior.
import { beforeEach, expect, it, vi } from 'vitest';

const hooks = vi.hoisted(() => ({ listener: null }));
vi.mock('../../../clio/ui/static/src/api.js', () => ({ api: vi.fn(), apiWithMeta: vi.fn(), icon: () => '' }));
vi.mock('../../../clio/ui/static/src/task-center.js', () => ({
  subscribeTaskEvents: listener => { hooks.listener = listener; return () => {}; },
  fetchTask: async () => null,
}));
vi.mock('../../../clio/ui/static/src/sidebar.js', () => ({
  loadVideos: vi.fn(async () => {}), loadPlans: vi.fn(async () => {}),
  renderVideoList: vi.fn(), saveProject: vi.fn(async () => {}), selectPlan: vi.fn(),
}));
vi.mock('../../../clio/ui/static/src/notification-center.js', () => ({ registerNotification: vi.fn() }));

import { api, apiWithMeta } from '../../../clio/ui/static/src/api.js';
import { state } from '../../../clio/ui/static/src/state.js';
import { renderRun, _handleRunStatus } from '../../../clio/ui/static/src/runner.js';

const steps = ['compress', 'transcribe', 'analyze', 'consistency', 'voiceover', 'plan', 'plan_voiceover', 'label'];
const task = (id, status, day = 'day2') => ({
  id, kind: 'pipeline', project_id: 'review-project', status, phase: 'analyze',
  current: 1, total: 2, input_summary: { steps: ['analyze'], day_label: day },
});

beforeEach(async () => {
  document.body.innerHTML = '';
  await _handleRunStatus({ status: 'idle' });
  vi.clearAllMocks();
  localStorage.clear();
  state.currentEntity = 'run';
  state.currentDay = 'day1';
  state.currentProjectDir = 'review-project';
  state.config = { output_dir: 'output' };
  state.selectionMode = false;
  state.selectedFiles = [];
  state.deps = null;
  api.mockImplementation(async (method, url) => {
    if (url === '/api/run/preview') return { preview: {
      steps: steps.map(name => ({ name, total: 2, will_run: 2, will_skip: 0 })),
    } };
    if (url === '/api/run/start') return { ok: true, task_id: 'old-run' };
    if (url.startsWith('/api/tasks?')) return { tasks: [] };
    return { files: {} };
  });
  apiWithMeta.mockResolvedValue({ data: { segments: [] }, version: 'v1' });
});

async function start() {
  document.body.innerHTML = '<div id="tab-run"></div>';
  renderRun();
  await vi.waitFor(() => expect(document.getElementById('btn-run-start').disabled).toBe(false));
  await document.getElementById('btn-run-start').onclick();
}

it('ignores a new task id after the previous run, including Task Center retries', async () => {
  await start();
  await _handleRunStatus({ status: 'error', message: 'old failed' });
  hooks.listener({ task: task('new-retry', 'running'), event: { message: 'retry running' } });
  expect(document.getElementById('run-progress').textContent).toContain('old failed');
  expect(document.querySelector('[aria-current="step"]')).toBeNull();
});

it('repeats completion side effects for two distinct events with a terminal task snapshot', async () => {
  await start();
  hooks.listener({ seq: 10, task: task('old-run', 'succeeded'), event: { message: 'last progress' } });
  await vi.waitFor(() => expect(document.getElementById('btn-run-view-result')).not.toBeNull());
  hooks.listener({ seq: 11, task: task('old-run', 'succeeded'), event: { message: 'completed' } });
  expect(document.getElementById('run-progress').textContent).toContain('completed');
  await vi.waitFor(() => expect(apiWithMeta.mock.calls.filter(([, url]) => url.startsWith('/api/plan?'))).toHaveLength(2));
});

it('loads the page day instead of the attached active task day after reconnect', async () => {
  api.mockImplementation(async (method, url) => {
    if (url.startsWith('/api/tasks?')) return { tasks: [task('old-run', 'running', 'day2')] };
    if (url === '/api/run/preview') return { preview: {
      steps: steps.map(name => ({ name, total: 2, will_run: 2, will_skip: 0 })),
    } };
    return { files: {} };
  });
  document.body.innerHTML = '<div id="tab-run"></div>';
  renderRun();
  await vi.waitFor(() => expect(document.querySelector('[aria-current="step"]')?.dataset.stage).toBe('ai_analysis'));
  hooks.listener({ task: task('old-run', 'succeeded', 'day2'), event: { message: 'completed' } });
  await vi.waitFor(() => expect(apiWithMeta).toHaveBeenCalled());
  expect(apiWithMeta.mock.calls[0][1]).toBe('/api/plan?day=day1');
  expect(state.currentDay).toBe('day1');
  await vi.waitFor(() => expect(document.getElementById('btn-run-view-result')).not.toBeNull());
});

it('retains a stale guide preview after cancellation', async () => {
  await start();
  api.mockClear();
  await _handleRunStatus({ status: 'cancelled', message: 'cancelled with partial output' });
  expect(api.mock.calls.some(([, url]) => url === '/api/run/preview')).toBe(false);
  expect(document.getElementById('btn-run-start').textContent).toContain('素材准备');
});

it('replaces an unsaved plan buffer when an unrelated compression task completes', async () => {
  await start();
  state.currentEntity = 'plan';
  state.dirty = true;
  state.plan = { day_title: 'Unsaved manual work', sequence: [] };
  await _handleRunStatus({ status: 'done', steps: ['compress'] });
  expect(state.plan).toEqual({ segments: [] });
  expect(state.dirty).toBe(true);
  state.dirty = false;
});
