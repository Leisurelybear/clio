import { beforeEach, expect, it, vi } from 'vitest';

const hooks = vi.hoisted(() => ({ listener: null }));
vi.mock('../api.js', () => ({ api: vi.fn(), icon: () => '' }));
vi.mock('../task-center.js', () => ({
  subscribeTaskEvents: listener => { hooks.listener = listener; return () => {}; },
  fetchTask: async () => null,
}));
vi.mock('../sidebar.js', () => ({
  loadVideos: vi.fn(async () => {}), loadPlans: vi.fn(async () => {}),
  renderVideoList: vi.fn(), saveProject: vi.fn(async () => {}), selectPlan: vi.fn(),
}));
vi.mock('../notification-center.js', () => ({ registerNotification: vi.fn() }));

import { api } from '../api.js';
import { state } from '../state.js';
import { renderRun, _handleRunStatus } from '../runner.js';
import { loadPlans, loadVideos, selectPlan } from '../sidebar.js';
import { RunTaskSession } from '../runner-task-session.js';

const steps = ['compress', 'transcribe', 'analyze', 'consistency', 'voiceover', 'plan', 'plan_voiceover', 'label'];
let caseId = 0;
let project;
const task = (id, status, day = 'day2') => ({
  id, kind: 'pipeline', project_id: project, status, phase: 'analyze',
  current: 1, total: 2, input_summary: { steps: ['plan'], day_label: day },
});

beforeEach(async () => {
  document.body.innerHTML = '';
  await _handleRunStatus({ status: 'idle' });
  vi.clearAllMocks();
  localStorage.clear();
  project = `review-project-${++caseId}`;
  state.currentEntity = 'run';
  state.currentDay = 'day1';
  state.currentProjectDir = project;
  state.config = { output_dir: 'output' };
  state.selectionMode = false;
  state.selectedFiles = [];
  state.deps = null;
  state.dirty = false;
  api.mockImplementation(async (method, url) => {
    if (url === '/api/run/preview') return { preview: {
      steps: steps.map(name => ({ name, total: 2, will_run: 2, will_skip: 0 })),
    } };
    if (url === '/api/run/start') return { ok: true, task_id: 'old-run' };
    if (url.startsWith('/api/tasks?')) return { tasks: [] };
    return { files: {} };
  });
});

async function start() {
  document.body.innerHTML = '<div id="tab-run"></div>';
  renderRun();
  await vi.waitFor(() => expect(document.getElementById('btn-run-start').disabled).toBe(false));
  await document.getElementById('btn-run-start').onclick();
}

it('hands off to a Task Center retry after the previous task failed', async () => {
  await start();
  hooks.listener({ task: task('old-run', 'failed'), seq: 1 });
  await vi.waitFor(() => expect(document.getElementById('btn-run-start').disabled).toBe(false));
  hooks.listener({ task: { ...task('new-retry', 'running'), retry_of: 'old-run' }, seq: 2 });
  await vi.waitFor(() => expect(document.querySelector('[aria-current="step"]')?.dataset.stage).toBe('ai_analysis'));
  expect(document.getElementById('btn-run-start').disabled).toBe(true);
});

it('attaches a retry that is already terminal in its first stream snapshot', async () => {
  await start();
  hooks.listener({ task: task('old-run', 'failed'), seq: 1 });
  await vi.waitFor(() => expect(document.getElementById('btn-run-start').disabled).toBe(false));
  hooks.listener({ task: { ...task('fast-retry', 'succeeded'), retry_of: 'old-run' }, seq: 2 });
  await vi.waitFor(() => expect(document.getElementById('btn-run-view-result')).not.toBeNull());
  expect(loadPlans).toHaveBeenCalledTimes(1);
});

it('preserves managed task result summary on the completion card', async () => {
  await start();
  hooks.listener({
    task: {
      ...task('old-run', 'succeeded'),
      input_summary: { steps: ['analyze'], day_label: 'day2' },
      result_summary: { steps: [{ name: 'analyze', processed: 3, failed: 0 }] },
    },
    seq: 1,
  });
  await vi.waitFor(() => expect(document.getElementById('run-completion-actions').textContent).toContain('AI 分析'));
  expect(document.getElementById('run-completion-actions').textContent).toContain('3');
});

it('restores completed task controls on render without repeating completion side effects', async () => {
  await start();
  hooks.listener({ task: task('old-run', 'succeeded'), seq: 1 });
  await vi.waitFor(() => expect(document.getElementById('btn-run-view-result')).not.toBeNull());
  expect(loadPlans).toHaveBeenCalledTimes(1);
  renderRun();
  await vi.waitFor(() => expect(document.getElementById('btn-run-view-result')).not.toBeNull());
  expect(loadPlans).toHaveBeenCalledTimes(1);
  document.getElementById('btn-run-view-result').click();
  await vi.waitFor(() => expect(selectPlan).toHaveBeenCalledWith('day2'));
});

it('abandons an old completion after switching projects during an awaited refresh', async () => {
  await start();
  let resolvePlans;
  loadPlans.mockImplementationOnce(() => new Promise(resolve => { resolvePlans = resolve; }));
  hooks.listener({ task: task('old-run', 'succeeded'), seq: 1 });
  await vi.waitFor(() => expect(resolvePlans).toBeTypeOf('function'));
  state.currentProjectDir = 'other-project';
  renderRun();
  resolvePlans();
  await new Promise(resolve => setTimeout(resolve, 0));
  expect(loadVideos).not.toHaveBeenCalled();
  expect(document.getElementById('btn-run-view-result')).toBeNull();
});

it('processes the new project while an old completion refresh is still pending', async () => {
  await start();
  let resolvePlans;
  loadPlans.mockImplementationOnce(() => new Promise(resolve => { resolvePlans = resolve; }));
  hooks.listener({ task: task('old-run', 'succeeded'), seq: 1 });
  await vi.waitFor(() => expect(resolvePlans).toBeTypeOf('function'));
  project = 'new-project';
  state.currentProjectDir = project;
  renderRun();
  hooks.listener({ task: task('new-run', 'running'), seq: 2 });
  await vi.waitFor(() => expect(document.querySelector('[aria-current="step"]')?.dataset.stage).toBe('ai_analysis'));
  expect(document.getElementById('btn-run-start').disabled).toBe(true);
  resolvePlans();
});

it('uses the terminal task message when a replayed progress event carries old text', async () => {
  await start();
  hooks.listener({
    task: { ...task('old-run', 'succeeded'), message: 'All requested work finished' },
    event: { message: 'Uploading source' }, seq: 1,
  });
  await vi.waitFor(() => expect(document.getElementById('run-progress').textContent).toContain('All requested work finished'));
  expect(document.getElementById('run-progress').querySelector('p:nth-child(2)').textContent).toBe('All requested work finished');
});

it('does not submit to another project after a delayed preflight preview', async () => {
  document.body.innerHTML = '<div id="tab-run"></div>';
  renderRun();
  await vi.waitFor(() => expect(document.getElementById('btn-run-start').disabled).toBe(false));
  let resolvePreview;
  api.mockImplementationOnce(() => new Promise(resolve => { resolvePreview = resolve; }));
  const started = document.getElementById('btn-run-start').onclick();
  state.currentProjectDir = 'other-project';
  resolvePreview({ preview: {} });
  await started;
  expect(api.mock.calls.some(([, url]) => url === '/api/run/start')).toBe(false);
});

it('finalizes a terminal task once across distinct stream events', async () => {
  await start();
  hooks.listener({ seq: 10, task: task('old-run', 'succeeded'), event: { message: 'last progress' } });
  await vi.waitFor(() => expect(document.getElementById('btn-run-view-result')).not.toBeNull());
  hooks.listener({ seq: 11, task: task('old-run', 'succeeded'), event: { message: 'completed' } });
  await new Promise(resolve => setTimeout(resolve, 0));
  expect(loadPlans).toHaveBeenCalledTimes(1);
  expect(api.mock.calls.filter(([, url]) => url === '/api/run/preview')).toHaveLength(3);
});

it('restores task day for explicit result navigation without changing editor state', async () => {
  api.mockImplementation(async (method, url) => {
    if (url.startsWith('/api/tasks?')) return { tasks: [task('attached', 'running', 'day2')] };
    if (url === '/api/run/preview') return { preview: {
      steps: steps.map(name => ({ name, total: 2, will_run: 2, will_skip: 0 })),
    } };
    return { files: {} };
  });
  document.body.innerHTML = '<div id="tab-run"></div>';
  renderRun();
  await vi.waitFor(() => expect(document.querySelector('[aria-current="step"]')?.dataset.stage).toBe('ai_analysis'));
  hooks.listener({ task: task('attached', 'succeeded', 'day2') });
  await vi.waitFor(() => expect(document.getElementById('btn-run-view-result')).not.toBeNull());
  expect(state.currentDay).toBe('day1');
  document.getElementById('btn-run-view-result').click();
  await vi.waitFor(() => expect(selectPlan).toHaveBeenCalledWith('day2'));
});

it.each(['cancelled', 'error'])('refreshes preview and clears force after %s', async status => {
  await start();
  document.getElementById('run-overwrite').checked = true;
  api.mockClear();
  await _handleRunStatus({ status, message: 'partial output' });
  expect(api.mock.calls.some(([, url]) => url === '/api/run/preview')).toBe(true);
  const preview = api.mock.calls.find(([, url]) => url === '/api/run/preview');
  expect(preview[2].overwrite).toBe(false);
});

it.each([['compress'], ['plan']])('preserves dirty plan and navigation when %s completes', async step => {
  await start();
  state.currentEntity = 'plan';
  state.dirty = true;
  const plan = { day_title: 'Unsaved manual work', sequence: [] };
  state.plan = plan;
  await _handleRunStatus({ status: 'done', steps: [step] });
  expect(state.plan).toBe(plan);
  expect(state.dirty).toBe(true);
  expect(state.currentDay).toBe('day1');
  expect(selectPlan).not.toHaveBeenCalled();
  expect(api.mock.calls.some(([, url]) => url.startsWith('/api/plan?'))).toBe(false);
});

it('shows a legacy succeeded task containing errors as incomplete', async () => {
  await start();
  hooks.listener({ task: { ...task('old-run', 'succeeded'), result_summary: { error_count: 1 } } });
  await vi.waitFor(() => expect(document.getElementById('run-progress').textContent).toContain('部分处理失败'));
  expect(document.getElementById('btn-run-view-result')).toBeNull();
});

it('rejects stale snapshots, wrong projects and repeated finalization', () => {
  const session = new RunTaskSession();
  session.resetProject(project);
  expect(session.accept(task('a', 'running'), { seq: 5 })).toBe(true);
  expect(session.accept(task('a', 'running'), { seq: 4 })).toBe(false);
  expect(session.accept({ ...task('b', 'running'), project_id: 'other' })).toBe(false);
  expect(session.accept(task('a', 'succeeded'), { seq: 6 })).toBe(true);
  expect(session.accept(task('a', 'running'), { attach: true })).toBe(false);
  expect(session.finalize('a')).toBe(true);
  expect(session.finalize('a')).toBe(false);
  expect(session.accept(task('a', 'running'), { seq: 7 })).toBe(false);
  expect(session.accept({ ...task('b', 'running'), retry_of: 'a' })).toBe(true);
  expect(session.accept(task('a', 'running'), { seq: 5 })).toBe(false);
});
