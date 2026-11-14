import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RUN_STAGES, buildRunStageModel, nextIncompleteStage } from '../runner-stage-model.js';
import { renderStageBoard } from '../runner-stages.js';

vi.mock('../api.js', () => ({ api: vi.fn(), apiWithMeta: vi.fn(), icon: () => '' }));
vi.mock('../task-center.js', () => ({ subscribeTaskEvents: () => () => {}, fetchTask: async () => null }));
vi.mock('../notification-center.js', () => ({ registerNotification: vi.fn() }));
vi.mock('../sidebar.js', () => ({
  loadVideos: async () => {}, loadPlans: async () => {}, renderVideoList: () => {},
  saveProject: () => {}, selectPlan: vi.fn(),
}));
import { api } from '../api.js';
import { renderRun, _handleRunStatus } from '../runner.js';
import { state } from '../state.js';

const allSteps = RUN_STAGES.flatMap(stage => stage.steps);
const preview = () => ({ steps: allSteps.map(name => ({ name, total: 2, will_run: 2, will_skip: 0 })) });

describe('guided run stages', () => {
  beforeEach(async () => {
    document.body.innerHTML = '';
    await _handleRunStatus({ status: 'idle' });
    localStorage.removeItem('vlog_ui_run_steps');
    state.currentEntity = 'run';
    state.selectionMode = false;
    state.selectedFiles = [];
    state.deps = null;
    state.config = { output_dir: 'output' };
    vi.mocked(api).mockReset();
    vi.mocked(api).mockImplementation(async (method, url) => {
      if (url === '/api/run/preview') return { preview: preview() };
      if (url === '/api/run/start') return { ok: true };
      return { files: {} };
    });
  });

  it('skips reusable stages but does not treat missing inputs as completed', () => {
    const p = preview();
    p.steps.slice(0, 2).forEach(row => { row.will_run = 0; row.will_skip = 2; });
    const model = buildRunStageModel({ selectedSteps: allSteps, preview: p });
    expect(model[0].status).toBe('reusable');
    expect(nextIncompleteStage(model).steps).toEqual(['analyze']);
    p.steps[2] = { name: 'analyze', total: 0, warnings: ['Missing compressed input'] };
    expect(nextIncompleteStage(buildRunStageModel({ selectedSteps: allSteps, preview: p })).status).toBe('blocked');
  });

  it('highlights only the current stage and escapes warning content', () => {
    const p = preview();
    p.steps[2].warnings = ['<img src=x onerror=alert(1)>'];
    const board = document.createElement('div');
    renderStageBoard(board, buildRunStageModel({ selectedSteps: allSteps, preview: p, activePhase: 'analyze' }));
    expect(board.querySelectorAll('[aria-current="step"]')).toHaveLength(1);
    expect(board.querySelector('[aria-current="step"]').dataset.stage).toBe('ai_analysis');
    expect(board.querySelector('img')).toBeNull();
    expect(board.textContent).toContain('<img');
  });

  it('submits only the advertised next stage and preserves the custom selections', async () => {
    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    await vi.waitFor(() => expect(document.getElementById('btn-run-start').textContent).toContain('素材准备'));
    expect(document.getElementById('run-custom-steps').open).toBe(false);
    expect(document.querySelectorAll('.run-stage-card')).toHaveLength(4);
    await document.getElementById('btn-run-start').onclick();
    const call = vi.mocked(api).mock.calls.find(([, url]) => url === '/api/run/start');
    expect(call[2].steps).toEqual(['compress', 'transcribe']);
    expect(document.querySelectorAll('.run-step-cb:checked')).toHaveLength(8);
  });

  it('keeps the run page after completion and opens results only on click', async () => {
    const { selectPlan } = await import('../sidebar.js');
    vi.mocked(selectPlan).mockClear();
    document.body.innerHTML = '<div id="run-progress"></div><div id="run-completion-actions"></div>';
    await _handleRunStatus({ status: 'done', steps: ['plan'], message: '<script>bad</script>' });
    expect(selectPlan).not.toHaveBeenCalled();
    expect(state.currentEntity).toBe('run');
    expect(document.querySelector('script')).toBeNull();
    document.getElementById('btn-run-view-result').click();
    await vi.waitFor(() => expect(selectPlan).toHaveBeenCalled());
  });

  it('switches to custom execution when checkboxes change', async () => {
    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    await vi.waitFor(() => expect(document.getElementById('btn-run-start').disabled).toBe(false));
    document.querySelectorAll('.run-step-cb').forEach(cb => { cb.checked = cb.dataset.step === 'analyze'; });
    document.querySelector('.run-step-cb').dispatchEvent(new Event('change'));
    await document.getElementById('btn-run-start').onclick();
    const call = vi.mocked(api).mock.calls.find(([, url]) => url === '/api/run/start');
    expect(call[2].steps).toEqual(['analyze']);
  });

  it('offers recovery after a failed preview and does not start an unknown stage', async () => {
    vi.mocked(api).mockRejectedValue(new Error('offline'));
    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    await vi.waitFor(() => expect(document.getElementById('btn-run-retry-preview').hidden).toBe(false));
    expect(document.getElementById('btn-run-start').disabled).toBe(true);
    vi.mocked(api).mockResolvedValue({ preview: preview() });
    await document.getElementById('btn-run-retry-preview').onclick();
    expect(document.getElementById('btn-run-start').disabled).toBe(false);
    expect(document.getElementById('btn-run-retry-preview').hidden).toBe(true);
  });

  it('reveals scope and automatically opens warnings for review', async () => {
    vi.mocked(api).mockResolvedValue({ preview: { ...preview(), totals: { warnings: 1 } } });
    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    await vi.waitFor(() => expect(document.getElementById('run-execution-options').open).toBe(true));
    expect(document.getElementById('run-scope').contains(document.getElementById('run-day'))).toBe(true);
  });

  it('uses real progress and omits an absent ETA', async () => {
    document.body.innerHTML = '<div id="run-progress"></div><button id="btn-run-start"></button>';
    await _handleRunStatus({ status: 'running', running: true, phase: 'analyze', current: 1, total: 4, progress_pct: 37 });
    expect(document.getElementById('run-progress').textContent).toContain('37%');
    expect(document.getElementById('run-progress').textContent).not.toContain('预计剩余');
  });
});


it('advances past explicitly disabled optional steps while retaining missing-input blocks', () => {
  const p = preview();
  p.steps[0] = { name: 'compress', total: 2, will_run: 0, will_skip: 2, warnings: [] };
  p.steps[1] = { name: 'transcribe', total: 0, will_run: 0, will_skip: 0, disabled: true, warnings: ['disabled'] };
  const m = buildRunStageModel({ selectedSteps: allSteps, preview: p });
  expect(m[0].status).toBe('reusable');
  expect(nextIncompleteStage(m).steps).toEqual(['analyze']);
  p.steps[1].disabled = false;
  expect(nextIncompleteStage(buildRunStageModel({ selectedSteps: allSteps, preview: p })).status).toBe('blocked');
});
