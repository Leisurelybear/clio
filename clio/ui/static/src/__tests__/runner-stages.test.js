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
import { renderRun, _handleRunStatus, collectRunOptions } from '../runner.js';
import { state } from '../state.js';

const allSteps = RUN_STAGES.flatMap(stage => stage.steps);
const preview = () => ({
  steps: allSteps.map(name => ({ name, total: 2, will_run: 2, will_skip: 0 })),
  stages: RUN_STAGES.map(stage => ({ key: stage.key, steps: stage.steps, status: 'ready',
    total: stage.steps.length * 2, will_run: stage.steps.length * 2, will_skip: 0, warnings: [],
    estimate: { planned_calls: 0 }, output: null })),
  next_stage: RUN_STAGES[0].key, blockers: [], outputs: [],
});

describe('guided run stages', () => {
  beforeEach(async () => {
    document.body.innerHTML = '';
    await _handleRunStatus({ status: 'idle' });
    localStorage.removeItem('vlog_ui_run_steps');
    localStorage.removeItem('vlog_ui_run_session');
    state.currentProjectDir = '';
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
    p.stages[0].status = 'reusable';
    p.next_stage = 'ai_analysis';
    const model = buildRunStageModel({ selectedSteps: allSteps, preview: p });
    expect(model[0].status).toBe('reusable');
    expect(nextIncompleteStage(model, p).steps).toEqual(['analyze']);
    p.steps[2] = { name: 'analyze', total: 0, warnings: ['Missing compressed input'] };
    p.stages[1].status = 'blocked';
    p.stages[1].warnings = ['Missing compressed input'];
    p.next_stage = 'ai_analysis';
    expect(nextIncompleteStage(buildRunStageModel({ selectedSteps: allSteps, preview: p }), p).status).toBe('blocked');
  });

  it('uses backend stage decisions and estimates instead of deriving them from step rows', () => {
    const p = preview();
    p.steps[0] = { name: 'compress', total: 0, will_run: 0, will_skip: 0, warnings: ['step row disagrees'] };
    p.stages[0].status = 'ready';
    p.stages[0].estimate = { planned_calls: 3, estimate_status: 'estimated', estimated_cost_usd: 0.12 };
    p.next_stage = 'source_prep';
    const model = buildRunStageModel({ selectedSteps: allSteps, preview: p });
    expect(model[0].status).toBe('ready');
    expect(model[0].estimate.planned_calls).toBe(3);
    expect(nextIncompleteStage(model, p).key).toBe('source_prep');
  });

  it('enriches the next-stage card with kicker, progress, and reassurance', async () => {
    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    await vi.waitFor(() => expect(document.getElementById('btn-run-start').disabled).toBe(false));
    expect(document.getElementById('run-next-kicker').textContent).toContain('下一步');
    expect(document.getElementById('run-next-kicker').textContent).toContain('1 / 4');
    expect(document.getElementById('run-next-title').textContent).toContain('素材准备');
    expect(document.getElementById('run-next-reassure').textContent).toContain('不会重复执行');
  });

  it('keeps waiting stages out of the guided next action', () => {
    const p = preview();
    p.stages[3].status = 'waiting';
    p.stages[3].deferred = true;
    p.stages[3].waiting_on = ['plan'];
    p.next_stage = null;
    const model = buildRunStageModel({ selectedSteps: allSteps, preview: p });
    expect(model[3].status).toBe('waiting');
    expect(model[3].waitingOn).toEqual(['plan']);
    expect(nextIncompleteStage(model, p)).toBeNull();
  });

  it('derives waiting status without treating deferred rows as blockers', () => {
    const p = { ...preview(), stages: undefined, next_stage: undefined };
    p.steps = p.steps.map(row => ({ ...row }));
    p.steps[6] = {
      name: 'plan_voiceover',
      total: 1,
      will_run: 0,
      will_run_max: 1,
      will_skip: 0,
      will_skip_min: 0,
      deferred: true,
      waiting_on: ['plan'],
      warnings: [],
    };
    const model = buildRunStageModel({ selectedSteps: ['plan_voiceover'], preview: p });
    expect(model[3].status).toBe('waiting');
    expect(model[3].blockers).toEqual([]);
    expect(nextIncompleteStage(model, p)).toBeNull();
  });

  it('marks a stage containing only disabled steps as disabled', () => {
    const p = { ...preview(), stages: undefined, next_stage: undefined };
    p.steps = [{
      name: 'transcribe', total: 0, will_run: 0, will_skip: 0, disabled: true,
      warnings: ['disabled'],
    }];
    const model = buildRunStageModel({ selectedSteps: ['transcribe'], preview: p });
    expect(model[0].status).toBe('disabled');
    expect(nextIncompleteStage(model, p)).toBeNull();
  });

  it('does not count disabled stages in guided progress', async () => {
    const p = preview();
    p.stages[0].status = 'disabled';
    p.next_stage = 'ai_analysis';
    vi.mocked(api).mockResolvedValue({ preview: p });
    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    await vi.waitFor(() => expect(document.getElementById('run-next-kicker').textContent).toContain('1 / 3'));
    expect(document.getElementById('run-next-kicker').textContent).toContain('下一步');
  });

  it('shows count ranges and a minimum-estimate note on cascaded stages', () => {
    const p = preview();
    p.stages[0].will_run = 1;
    p.stages[0].will_run_max = 3;
    p.stages[0].will_skip = 3;
    p.stages[0].will_skip_min = 1;
    p.stages[0].estimate_is_minimum = true;
    p.stages[0].step_counts = [
      {
        name: 'compress', total: 2, will_run: 1, will_run_max: 3,
        will_skip: 3, will_skip_min: 1,
      },
      {
        name: 'transcribe', total: 2, will_run: 1, will_run_max: 3,
        will_skip: 3, will_skip_min: 1,
      },
    ];
    const board = document.createElement('div');
    renderStageBoard(board, buildRunStageModel({ selectedSteps: allSteps, preview: p }));
    const card = board.querySelector('[data-stage="source_prep"]');
    expect(card.querySelectorAll('.run-stage-step-count')).toHaveLength(2);
    expect(card.textContent).toContain('压缩原视频待执行 1–3，跳过 1–3');
    expect(card.textContent).toContain('语音转录待执行 1–3，跳过 1–3');
    expect(card.textContent).toContain('估算为下限');
  });

  it('explains which steps caused a stage to rerun', () => {
    const p = preview();
    p.stages[1].cascade_from = ['compress'];
    const board = document.createElement('div');
    renderStageBoard(board, buildRunStageModel({ selectedSteps: allSteps, preview: p }));
    expect(board.querySelector('[data-stage="ai_analysis"] .run-stage-cascade').textContent)
      .toContain('同批次');
  });

  it('renders the disabled stage status without a reusable-action affordance', () => {
    const p = preview();
    p.stages[0].status = 'disabled';
    p.stages[0].output = { target: 'video', steps: ['transcribe'] };
    const board = document.createElement('div');
    renderStageBoard(board, buildRunStageModel({ selectedSteps: allSteps, preview: p }));
    const card = board.querySelector('[data-stage="source_prep"]');
    expect(card.textContent).toContain('本次已关闭');
    expect(card.querySelector('[data-stage-output]')).toBeNull();
  });

  it('explains partial waiting inside an otherwise ready stage', () => {
    const p = preview();
    p.stages[3].waiting_on = ['plan'];
    const board = document.createElement('div');
    renderStageBoard(board, buildRunStageModel({ selectedSteps: allSteps, preview: p }));
    expect(board.querySelector('[data-stage="voiceover_delivery"] .run-stage-waiting').textContent)
      .toContain('部分步骤');
    expect(board.querySelector('[data-stage="voiceover_delivery"] .run-stage-waiting').textContent)
      .toContain('规划');
  });

  it('renders a plain-language description for each stage status', () => {
    const p = preview();
    p.stages[0].status = 'reusable';
    const board = document.createElement('div');
    renderStageBoard(board, buildRunStageModel({ selectedSteps: allSteps, preview: p }));
    const reusableCard = board.querySelector('[data-stage="source_prep"]');
    expect(reusableCard.querySelector('.run-stage-status-desc').textContent)
      .toContain('不会重复执行');
    expect(board.querySelectorAll('.run-stage-status-desc')).toHaveLength(4);
  });

  it('explains why a blocked stage cannot run and mirrors blockers on the next card', async () => {
    const p = preview();
    p.steps[2] = { name: 'analyze', total: 0, warnings: ['Missing compressed input'] };
    p.stages[1].status = 'blocked';
    p.stages[1].warnings = ['Missing compressed input'];
    p.stages[1].blockers = ['Missing compressed input'];
    p.next_stage = 'ai_analysis';
    const board = document.createElement('div');
    renderStageBoard(board, buildRunStageModel({ selectedSteps: allSteps, preview: p }));
    expect(board.textContent).toContain('阻塞原因');
    expect(board.textContent).toContain('Missing compressed input');

    vi.mocked(api).mockResolvedValue({ preview: p });
    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    await vi.waitFor(() => expect(document.getElementById('run-next-blockers').hidden).toBe(false));
    const blockers = document.getElementById('run-next-blockers');
    expect(document.getElementById('btn-run-start').disabled).toBe(true);
    expect(blockers.textContent).toContain('阻塞原因');
    expect(blockers.textContent).toContain('Missing compressed input');
  });

  it('highlights only the current stage and escapes warning content', () => {
    const p = preview();
    p.stages[1].warnings = ['<img src=x onerror=alert(1)>'];
    const board = document.createElement('div');
    renderStageBoard(board, buildRunStageModel({ selectedSteps: allSteps, preview: p, activePhase: 'analyze' }));
    expect(board.querySelectorAll('[aria-current="step"]')).toHaveLength(1);
    expect(board.querySelector('[aria-current="step"]').dataset.stage).toBe('ai_analysis');
    expect(board.querySelector('img')).toBeNull();
    expect(board.textContent).toContain('<img');
  });

  it('passes the authoritative output target from reusable stages', () => {
    const p = preview();
    p.stages[0].status = 'reusable';
    p.stages[0].output = { target: 'texts', steps: ['compress'] };
    const board = document.createElement('div');
    const openOutput = vi.fn();
    renderStageBoard(board, buildRunStageModel({ selectedSteps: allSteps, preview: p }), openOutput);

    board.querySelector('[data-stage-output="source_prep"]').click();
    expect(openOutput).toHaveBeenCalledWith(expect.objectContaining({
      key: 'source_prep',
      output: { target: 'texts', steps: ['compress'] },
    }));
  });

  it('uses the shared dynamic precision for next-stage cost estimates', async () => {
    const p = preview();
    p.stages[0].estimate = {
      planned_calls: 2,
      estimate_status: 'estimated',
      estimated_cost_usd: 0.013,
    };
    p.next_stage = 'source_prep';
    vi.mocked(api).mockResolvedValue({ preview: p });
    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    await vi.waitFor(() => expect(document.getElementById('run-next-estimate').textContent).toContain('$0.0130'));
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

  it('does not inherit a previous custom step selection into guided mode', async () => {
    localStorage.setItem('vlog_ui_run_steps', JSON.stringify({
      steps: { compress: false, transcribe: false, analyze: true, consistency: false,
        voiceover: false, plan: false, plan_voiceover: false, label: false },
      use_transcripts: true,
    }));
    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();

    await vi.waitFor(() => expect(document.getElementById('btn-run-start').disabled).toBe(false));
    expect(document.querySelectorAll('.run-step-cb:checked')).toHaveLength(8);
    expect(document.getElementById('run-custom-steps').open).toBe(false);
    expect(document.getElementById('btn-run-start').textContent).toContain('素材准备');
  });

  it('keeps the run page after completion and opens results only on click', async () => {
    const { selectPlan } = await import('../sidebar.js');
    vi.mocked(selectPlan).mockClear();
    document.body.innerHTML = '<div id="run-progress"></div><div id="run-completion-actions"></div>';
    await _handleRunStatus({ status: 'done', steps: ['plan'], message: '<script>bad</script>' });
    expect(selectPlan).not.toHaveBeenCalled();
    expect(state.currentEntity).toBe('run');
    expect(document.querySelector('script')).toBeNull();
    expect(document.getElementById('run-completion-actions').textContent).toContain('本次运行未产生新的文件');
    document.getElementById('btn-run-view-result').click();
    await vi.waitFor(() => expect(selectPlan).toHaveBeenCalled());
  });

  it('summarizes processed outputs on the completion card', async () => {
    document.body.innerHTML = '<div id="run-progress"></div><div id="run-completion-actions"></div>';
    await _handleRunStatus({
      status: 'done',
      steps: ['analyze'],
      message: 'done',
      result_summary: { steps: [{ name: 'analyze', processed: 3, failed: 0 }] },
    });
    const actions = document.getElementById('run-completion-actions');
    expect(actions.textContent).toContain('本次产出');
    expect(actions.textContent).toContain('AI 分析');
    expect(actions.textContent).toContain('已处理 3 项');
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

  it('surfaces custom mode with an inline recovery action', async () => {
    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    await vi.waitFor(() => expect(document.getElementById('btn-run-start').disabled).toBe(false));
    expect(document.getElementById('btn-run-guided').hidden).toBe(true);

    document.querySelectorAll('.run-step-cb').forEach(cb => { cb.checked = cb.dataset.step === 'analyze'; });
    document.querySelector('.run-step-cb').dispatchEvent(new Event('change'));
    await vi.waitFor(() => expect(document.getElementById('run-next-kicker').textContent).toContain('自定义模式'));
    expect(document.getElementById('run-next-description').textContent).toContain('按下方勾选执行');
    expect(document.getElementById('btn-run-guided').hidden).toBe(false);
    expect(document.getElementById('run-custom-steps').open).toBe(true);

    await document.getElementById('btn-run-guided').click();
    await vi.waitFor(() => expect(document.getElementById('run-next-kicker').textContent).not.toContain('自定义模式'));
    expect(document.getElementById('btn-run-guided').hidden).toBe(true);
    expect(document.querySelectorAll('.run-step-cb:checked')).toHaveLength(8);
  });

  it('offers one inline recovery after a failed preview and does not start an unknown stage', async () => {
    vi.mocked(api).mockRejectedValue(new Error('offline'));
    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    await vi.waitFor(() => expect(document.querySelector('.run-preview-retry')).not.toBeNull());
    expect(document.getElementById('btn-run-start').disabled).toBe(true);
    expect(document.getElementById('run-execution-options').open).toBe(true);
    expect(document.getElementById('run-preview').textContent).toContain('预览生成失败');
    expect(document.querySelectorAll('.run-preview-retry')).toHaveLength(1);
    expect(document.getElementById('btn-run-retry-preview')).toBeNull();
    vi.mocked(api).mockResolvedValue({ preview: preview() });
    document.querySelector('.run-preview-retry').click();
    await vi.waitFor(() => expect(document.getElementById('btn-run-start').disabled).toBe(false));
    expect(document.querySelector('.run-preview-retry')).toBeNull();
  });

  it('reveals scope and automatically opens warnings for review', async () => {
    vi.mocked(api).mockResolvedValue({ preview: { ...preview(), totals: { warnings: 1 } } });
    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    await vi.waitFor(() => expect(document.getElementById('run-execution-options').open).toBe(true));
    expect(document.getElementById('run-scope').contains(document.getElementById('run-day'))).toBe(true);
  });

  it('opens detailed estimates by default when the run will call AI', async () => {
    const p = preview();
    p.totals = { warnings: 0, planned_calls: 3, estimate_status: 'unknown', estimated_cost_usd: null };
    vi.mocked(api).mockResolvedValue({ preview: p });
    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    await vi.waitFor(() => expect(document.getElementById('run-execution-options').open).toBe(true));
  });

  it('uses real progress and omits an absent ETA', async () => {
    document.body.innerHTML = '<div id="run-progress"></div><button id="btn-run-start"></button>';
    await _handleRunStatus({ status: 'running', running: true, phase: 'analyze', current: 1, total: 4, progress_pct: 37 });
    expect(document.getElementById('run-progress').textContent).toContain('37%');
    expect(document.getElementById('run-progress').textContent).not.toContain('预计剩余');
  });

  it('keeps delivery highlighted when planned voiceover emits a shared runtime phase', async () => {
    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    await vi.waitFor(() => expect(document.getElementById('btn-run-start').disabled).toBe(false));
    for (const phase of ['plan_voiceover', 'voiceover', 'voiceover']) {
      await _handleRunStatus({ status: 'running', running: true, steps: ['plan_voiceover', 'label'], phase });
      expect(document.querySelector('[aria-current="step"]').dataset.stage).toBe('voiceover_delivery');
    }
  });

  it('consumes overwrite for one action and progresses using a fresh non-forced preview', async () => {
    let prepared = false;
    vi.mocked(api).mockImplementation(async (method, url, options) => {
      if (url !== '/api/run/preview') return url === '/api/run/start' ? { ok: true } : { files: {} };
      const p = preview();
      if (prepared && !options.overwrite) p.steps.slice(0, 2).forEach(row => {
        row.will_run = 0; row.will_skip = 2;
      });
      if (prepared && !options.overwrite) {
        p.stages[0].status = 'reusable';
        p.stages[0].will_run = 0;
        p.stages[0].will_skip = 4;
        p.next_stage = 'ai_analysis';
      }
      return { preview: p };
    });
    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    await vi.waitFor(() => expect(document.getElementById('btn-run-start').disabled).toBe(false));
    const overwrite = document.getElementById('run-overwrite');
    overwrite.checked = true;
    overwrite.dispatchEvent(new Event('change'));
    await vi.waitFor(() => expect(document.getElementById('btn-run-start').disabled).toBe(false));
    await document.getElementById('btn-run-start').onclick();
    const start = vi.mocked(api).mock.calls.find(([, url]) => url === '/api/run/start');
    expect(start[2]).toMatchObject({ steps: ['compress', 'transcribe'], overwrite: true });
    prepared = true;
    await _handleRunStatus({ status: 'done', steps: ['compress', 'transcribe'] });
    await vi.waitFor(() => expect(document.getElementById('btn-run-start').textContent).toContain('AI 看素材'));
    expect(overwrite.checked).toBe(false);
    expect(document.getElementById('btn-run-start').disabled).toBe(false);
  });

  it('distinguishes both voiceover tasks within a custom full pipeline', async () => {
    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    await vi.waitFor(() => expect(document.getElementById('btn-run-start').disabled).toBe(false));
    for (const [phase, expected] of [['voiceover', 'plan_alignment'], ['plan', 'plan_alignment'],
      ['plan_voiceover', 'voiceover_delivery'], ['voiceover', 'voiceover_delivery']]) {
      await _handleRunStatus({ status: 'running', running: true, steps: allSteps, phase });
      expect(document.querySelector('[aria-current="step"]').dataset.stage).toBe(expected);
    }
  });

  it('lets unavailable ASR be skipped after preparation completes without a transcript', async () => {
    vi.mocked(api).mockImplementation(async (method, url, options) => {
      if (url !== '/api/run/preview') return { files: {} };
      const p = preview();
      p.steps[0] = { name: 'compress', total: 2, will_run: 0, will_skip: 2 };
      p.steps[1] = { name: 'transcribe', total: 2, will_run: 2, will_skip: 0 };
      p.steps = p.steps.filter(row => options.steps.includes(row.name));
      p.stages[0].status = options.steps.includes('transcribe') ? 'ready' : 'reusable';
      p.stages[0].steps = options.steps.filter(step => RUN_STAGES[0].steps.includes(step));
      p.stages[0].will_run = options.steps.includes('transcribe') ? 2 : 0;
      p.stages[0].will_skip = 2;
      p.next_stage = options.steps.includes('transcribe') ? 'source_prep' : 'ai_analysis';
      return { preview: p };
    });
    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    await _handleRunStatus({ status: 'done', steps: ['compress', 'transcribe'] });
    await vi.waitFor(() => expect(document.getElementById('btn-run-skip-transcribe').hidden).toBe(false));
    await document.getElementById('btn-run-skip-transcribe').onclick();
    expect(document.getElementById('btn-run-start').textContent).toContain('AI 看素材');
    expect(document.querySelector('.run-step-cb[data-step="transcribe"]').checked).toBe(false);
    expect(document.getElementById('run-use-transcripts').checked).toBe(true);
    expect(JSON.parse(localStorage.getItem('vlog_ui_run_steps')).steps.transcribe).toBe(false);
  });

  it('advances automatically when preview explicitly disables optional transcription', async () => {
    const p = preview();
    p.steps[0] = { name: 'compress', total: 2, will_run: 0, will_skip: 2 };
    p.steps[1] = { name: 'transcribe', total: 0, disabled: true, warnings: ['Disabled by option'] };
    p.stages[0].status = 'reusable';
    p.stages[0].will_run = 0;
    p.stages[0].will_skip = 2;
    p.next_stage = 'ai_analysis';
    vi.mocked(api).mockResolvedValue({ preview: p });
    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    await vi.waitFor(() => expect(document.getElementById('btn-run-start').textContent).toContain('AI 看素材'));
    expect(document.getElementById('btn-run-start').disabled).toBe(false);
    expect(document.getElementById('btn-run-skip-transcribe').hidden).toBe(true);
  });
});


it('advances past explicitly disabled optional steps while retaining missing-input blocks', () => {
  const p = preview();
  p.steps[0] = { name: 'compress', total: 2, will_run: 0, will_skip: 2, warnings: [] };
  p.steps[1] = { name: 'transcribe', total: 0, will_run: 0, will_skip: 0, disabled: true, warnings: ['disabled'] };
  p.stages[0].status = 'reusable';
  p.stages[0].steps = ['compress', 'transcribe'];
  p.stages[0].will_run = 0;
  p.stages[0].will_skip = 2;
  p.next_stage = 'ai_analysis';
  const m = buildRunStageModel({ selectedSteps: allSteps, preview: p });
  expect(m[0].status).toBe('reusable');
  expect(nextIncompleteStage(m, p).steps).toEqual(['analyze']);
  p.steps[1].disabled = false;
  p.stages[0].status = 'blocked';
  p.next_stage = 'source_prep';
  expect(nextIncompleteStage(buildRunStageModel({ selectedSteps: allSteps, preview: p }), p).status).toBe('blocked');
});

describe('guided run session persistence', () => {
  const SESSION_KEY = 'vlog_ui_run_session';
  const session = () => JSON.parse(localStorage.getItem(SESSION_KEY) || 'null');

  beforeEach(async () => {
    document.body.innerHTML = '';
    await _handleRunStatus({ status: 'idle' });
    localStorage.clear();
    state.currentEntity = 'run';
    state.currentDay = 'day1';
    state.currentProjectDir = '';
    state.selectionMode = false;
    state.selectedFiles = [];
    state.deps = null;
    state.config = { output_dir: 'output' };
    vi.mocked(api).mockReset();
    vi.mocked(api).mockImplementation(async () => ({ files: {} }));
  });

  async function renderWithSkipAvailable(project = 'session-project') {
    state.currentProjectDir = project;
    state.currentDay = 'day3';
    vi.mocked(api).mockImplementation(async (method, url, options) => {
      if (url !== '/api/run/preview') return { files: {} };
      const p = preview();
      p.steps[0] = { name: 'compress', total: 2, will_run: 0, will_skip: 2 };
      p.steps[1] = { name: 'transcribe', total: 2, will_run: 2, will_skip: 0 };
      p.steps = p.steps.filter(row => options.steps.includes(row.name));
      p.stages[0].status = options.steps.includes('transcribe') ? 'ready' : 'reusable';
      p.stages[0].steps = options.steps.filter(step => RUN_STAGES[0].steps.includes(step));
      p.stages[0].will_run = options.steps.includes('transcribe') ? 2 : 0;
      p.stages[0].will_skip = 2;
      p.next_stage = options.steps.includes('transcribe') ? 'source_prep' : 'ai_analysis';
      return { preview: p };
    });
    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    await vi.waitFor(() => expect(document.getElementById('btn-run-skip-transcribe').hidden).toBe(false));
    document.getElementById('btn-run-skip-transcribe').click();
    await vi.waitFor(() => expect(document.getElementById('btn-run-start').textContent).toContain('AI 看素材'));
  }

  it('restores the skipped step and scope after a reload', async () => {
    await renderWithSkipAvailable();
    expect(session()).toMatchObject({ project: 'session-project', day: 'day3', custom: false });
    expect(session().skipped).toEqual(['transcribe']);

    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    expect(document.querySelector('.run-step-cb[data-step="transcribe"]').checked).toBe(false);
    expect(document.getElementById('run-day').value).toBe('day3');
    expect(collectRunOptions().steps).not.toContain('transcribe');
  });

  it('does not leak a guided session into another project', async () => {
    await renderWithSkipAvailable('session-project');
    document.body.innerHTML = '<div id="tab-run"></div>';
    state.currentProjectDir = 'other-project';
    renderRun();
    expect(document.querySelector('.run-step-cb[data-step="transcribe"]').checked).toBe(true);
    expect(collectRunOptions().steps).toContain('transcribe');
  });

  it('restores custom execution mode after a reload', async () => {
    await renderWithSkipAvailable();
    const analyze = document.querySelector('.run-step-cb[data-step="analyze"]');
    analyze.checked = false;
    analyze.dispatchEvent(new Event('change'));
    await vi.waitFor(() => expect(session()?.custom).toBe(true));

    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    expect(document.getElementById('run-custom-steps').open).toBe(true);
    expect(document.querySelector('.run-step-cb[data-step="analyze"]').checked).toBe(false);
  });

  it('clears the persisted skip when returning to guided stages', async () => {
    await renderWithSkipAvailable();
    document.getElementById('btn-run-guided').click();
    await vi.waitFor(() => expect(session()?.skipped).toEqual([]));

    document.body.innerHTML = '<div id="tab-run"></div>';
    renderRun();
    expect(document.querySelector('.run-step-cb[data-step="transcribe"]').checked).toBe(true);
  });
});
