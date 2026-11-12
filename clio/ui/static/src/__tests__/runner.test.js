import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  buildSkippedDiagnostics,
  missingMediaDepsForSteps,
  renderSkippedDiagnosticsHtml,
  collectRunOptions,
  startRun,
  _completionTargetForSteps,
  getRunButtonText,
  _shouldResetRunNavigationOnRender,
  updateRunStartButtonState,
  refreshVideosAfterRun,
  staleWarningHtml,
  _handleRunStatus,
} from '../runner.js';
import { state } from '../state.js';

vi.mock('../notification-center.js', () => ({
  registerNotification: vi.fn(() => Promise.resolve(null)),
}));

vi.mock('../confirm.js', () => ({
  confirmDialog: vi.fn(async () => true),
  alertDialog: vi.fn(async () => undefined),
  promptDialog: vi.fn(async () => ''),
}));

vi.mock('../sidebar.js', () => ({
  loadVideos: vi.fn(() => Promise.resolve()),
  loadPlans: vi.fn(() => Promise.resolve()),
  renderVideoList: vi.fn(),
  saveProject: vi.fn(() => Promise.resolve()),
}));

vi.mock('../api.js', () => ({
  api: vi.fn(),
  icon: vi.fn(() => ''),
}));

describe('missingMediaDepsForSteps', () => {
  it('does not block label when only ffprobe is missing', () => {
    expect(missingMediaDepsForSteps(['label'], { missing: ['ffprobe'] })).toEqual([]);
  });
});

describe('_shouldResetRunNavigationOnRender', () => {
  it('preserves completion navigation while a run is active', () => {
    expect(_shouldResetRunNavigationOnRender(true)).toBe(false);
  });

  it('resets stale navigation flags when idle', () => {
    expect(_shouldResetRunNavigationOnRender(false)).toBe(true);
  });
});

describe('collectRunOptions', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <input class="run-step-cb" data-step="compress" type="checkbox" checked>
      <input class="run-step-cb" data-step="analyze" type="checkbox" checked>
      <input class="run-step-cb" data-step="consistency" type="checkbox" checked>
      <input class="run-step-cb" data-step="plan" type="checkbox">
      <input id="run-day" value="day2">
      <input id="run-use-transcripts" type="checkbox" checked>
      <input id="run-overwrite" type="checkbox">
      <textarea id="run-context-override">[analyze] focus food</textarea>
    `;
  });

  it('collects checked steps and options from the run form', () => {
    const opts = collectRunOptions();
    expect(opts.steps).toEqual(['compress', 'analyze', 'consistency']);
    expect(opts.day_label).toBe('day2');
    expect(opts.use_transcripts).toBe(true);
    expect(opts.overwrite).toBe(false);
    expect(opts.context_override).toBe('[analyze] focus food');
  });

  it('marks overwrite when checkbox is checked', () => {
    document.getElementById('run-overwrite').checked = true;
    expect(collectRunOptions().overwrite).toBe(true);
  });

  it('defaults use_transcripts when checkbox is missing', () => {
    document.getElementById('run-use-transcripts').remove();
    expect(collectRunOptions().use_transcripts).toBe(true);
  });
});

describe('getRunButtonText / updateRunStartButtonState', () => {
  beforeEach(() => {
    state.selectionMode = false;
    state.selectedFiles = [];
    document.body.innerHTML = `<button id="btn-run-start" class="btn-primary"></button>`;
  });

  it('default label when not in selection mode', () => {
    expect(getRunButtonText()).toContain('运行选中步骤');
    expect(getRunButtonText()).not.toContain('请先勾选');
    expect(getRunButtonText()).not.toMatch(/\(\d+\)/);
  });

  it('prompts to select when selection mode with empty list', () => {
    state.selectionMode = true;
    state.selectedFiles = [];
    expect(getRunButtonText()).toContain('请先勾选视频');
  });

  it('includes selection count when files are checked', () => {
    state.selectionMode = true;
    state.selectedFiles = ['a.mp4', 'b.mp4', 'c.mp4'];
    expect(getRunButtonText()).toContain('(3)');
    expect(getRunButtonText()).toContain('运行选中步骤');
  });

  it('writes selection-aware label onto the start button', () => {
    state.selectionMode = true;
    state.selectedFiles = ['a.mp4'];
    updateRunStartButtonState();
    const btn = document.getElementById('btn-run-start');
    expect(btn.disabled).toBe(false);
    expect(btn.innerHTML).toContain('(1)');
    expect(btn.innerHTML).toContain('运行选中步骤');
  });

  it('disables start button when selection mode has no files', () => {
    state.selectionMode = true;
    state.selectedFiles = [];
    updateRunStartButtonState();
    const btn = document.getElementById('btn-run-start');
    expect(btn.disabled).toBe(true);
    expect(btn.innerHTML).toContain('请先勾选视频');
    expect(btn.title).toContain('勾选');
  });
});

describe('startRun', () => {
  const baseDom = `
    <button id="btn-run-start"></button>
    <div id="run-preview"></div>
    <div id="run-progress"></div>
    <input class="run-step-cb" data-step="plan" type="checkbox" checked>
    <input id="run-day" value="day2">
    <input id="run-use-transcripts" type="checkbox" checked>
    <input id="run-overwrite" type="checkbox">
  `;

  beforeEach(async () => {
    state.selectionMode = true;
    state.selectedFiles = ['A.mp4'];
    state.deps = null;
    document.body.innerHTML = baseDom;
    const { api } = await import('../api.js');
    vi.mocked(api).mockReset();
    const { confirmDialog } = await import('../confirm.js');
    vi.mocked(confirmDialog).mockReset();
    vi.mocked(confirmDialog).mockResolvedValue(true);
  });

  it('stops without starting when the overwrite confirmation is declined', async () => {
    const { api } = await import('../api.js');
    api.mockResolvedValueOnce({
      preview: { plan_overwrite: { required: true, day_label: 'day2' } },
    });
    const { confirmDialog } = await import('../confirm.js');
    vi.mocked(confirmDialog).mockResolvedValue(false);

    await startRun();

    expect(api).toHaveBeenCalledTimes(1);
    expect(api).toHaveBeenCalledWith('POST', '/api/run/preview', expect.objectContaining({
      files: ['A.mp4'],
      steps: ['plan'],
    }));
    expect(document.getElementById('btn-run-start').disabled).toBe(false);
    expect(api).not.toHaveBeenCalledWith('POST', '/api/run/start', expect.anything());
  });

  it('sends the confirmation flag after the user accepts overwrite', async () => {
    const { api } = await import('../api.js');
    api.mockResolvedValueOnce({
      preview: { plan_overwrite: { required: true, day_label: 'day2' } },
    });
    api.mockResolvedValueOnce({ ok: true, task_id: 'task-1' });

    await startRun();

    const { confirmDialog } = await import('../confirm.js');
    expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining('替换 day2 的现有计划'), expect.anything());
    const startCall = api.mock.calls.find(([method, url]) => method === 'POST' && url === '/api/run/start');
    expect(startCall?.[2]).toEqual(expect.objectContaining({
      files: ['A.mp4'],
      steps: ['plan'],
      confirm_plan_overwrite: true,
    }));
  });

  it('stops without starting when the artifact overwrite confirmation is declined', async () => {
    const { api } = await import('../api.js');
    api.mockResolvedValueOnce({
      preview: { artifact_overwrite: { required: true, items: ['001_A_voiceover.json'] } },
    });
    const { confirmDialog } = await import('../confirm.js');
    vi.mocked(confirmDialog).mockResolvedValue(false);

    await startRun();

    expect(api).toHaveBeenCalledTimes(1);
    expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining('001_A_voiceover.json'), expect.anything());
    expect(api).not.toHaveBeenCalledWith('POST', '/api/run/start', expect.anything());
  });

  it('sends the artifact confirmation flag after the user accepts', async () => {
    const { api } = await import('../api.js');
    api.mockResolvedValueOnce({
      preview: { artifact_overwrite: { required: true, items: ['001_A_voiceover.json'] } },
    });
    api.mockResolvedValueOnce({ ok: true, task_id: 'task-artifact' });

    await startRun();

    const startCall = api.mock.calls.find(([method, url]) => method === 'POST' && url === '/api/run/start');
    expect(startCall?.[2]).toEqual(expect.objectContaining({
      steps: ['plan'],
      confirm_artifact_overwrite: true,
    }));
    expect(startCall?.[2]).not.toHaveProperty('confirm_plan_overwrite');
  });

  it('refreshes the preview for a voiceover-only run', async () => {
    document.body.innerHTML = `
      <button id="btn-run-start"></button>
      <div id="run-preview"></div>
      <div id="run-progress"></div>
      <input class="run-step-cb" data-step="plan" type="checkbox">
      <input class="run-step-cb" data-step="voiceover" type="checkbox" checked>
      <input id="run-day" value="day1">
      <input id="run-use-transcripts" type="checkbox" checked>
      <input id="run-overwrite" type="checkbox">
    `;
    const { api } = await import('../api.js');
    api.mockResolvedValueOnce({ preview: {} });
    api.mockResolvedValueOnce({ ok: true, task_id: 'task-voiceover' });

    await startRun();

    expect(api).toHaveBeenNthCalledWith(1, 'POST', '/api/run/preview', expect.objectContaining({
      steps: ['voiceover'],
    }));
    const startCall = api.mock.calls.find(([method, url]) => method === 'POST' && url === '/api/run/start');
    expect(startCall?.[2]).toEqual(expect.objectContaining({ steps: ['voiceover'] }));
  });

  it('refreshes the preview for a plan-voiceover-only run', async () => {
    document.body.innerHTML = `
      <button id="btn-run-start"></button>
      <div id="run-preview"></div>
      <div id="run-progress"></div>
      <input class="run-step-cb" data-step="plan" type="checkbox">
      <input class="run-step-cb" data-step="plan_voiceover" type="checkbox" checked>
      <input id="run-day" value="day1">
      <input id="run-use-transcripts" type="checkbox" checked>
      <input id="run-overwrite" type="checkbox">
    `;
    const { api } = await import('../api.js');
    api.mockResolvedValueOnce({ preview: {} });
    api.mockResolvedValueOnce({ ok: true, task_id: 'task-plan-voiceover' });

    await startRun();

    expect(api).toHaveBeenNthCalledWith(1, 'POST', '/api/run/preview', expect.objectContaining({
      steps: ['plan_voiceover'],
    }));
    const startCall = api.mock.calls.find(([method, url]) => method === 'POST' && url === '/api/run/start');
    expect(startCall?.[2]).toEqual(expect.objectContaining({ steps: ['plan_voiceover'] }));
  });

  it('does not ask when the fresh preview reports no overwrite risk', async () => {
    const { api } = await import('../api.js');
    api.mockResolvedValueOnce({ preview: {} });
    api.mockResolvedValueOnce({ ok: true, task_id: 'task-2' });

    await startRun();

    const { confirmDialog } = await import('../confirm.js');
    expect(confirmDialog).not.toHaveBeenCalled();
    const startCall = api.mock.calls.find(([method, url]) => method === 'POST' && url === '/api/run/start');
    expect(startCall?.[2]).not.toHaveProperty('confirm_plan_overwrite');
  });
});

describe('_completionTargetForSteps', () => {
  it('opens the plan after planned voiceover generation', () => {
    expect(_completionTargetForSteps(['plan_voiceover'])).toEqual({ entity: 'plan' });
    expect(_completionTargetForSteps(['plan', 'plan_voiceover'])).toEqual({ entity: 'plan' });
  });
});

describe('staleWarningHtml', () => {
  it('shows a generic ASR hint only for the transcribe phase', () => {
    const html = staleWarningHtml('transcribe');
    expect(html).toContain('stale-warn');
    expect(html).toContain('ASR 引擎、模型或 API Key');
    expect(html).toContain('link-stale-settings');
  });

  it('omits the ASR hint for non-transcribe phases', () => {
    for (const phase of ['compress', 'analyze', 'voiceover', 'plan', 'plan_voiceover', 'label', '启动', '']) {
      const html = staleWarningHtml(phase);
      expect(html).toContain('stale-warn');
      expect(html).toContain('进度长时间未更新');
      expect(html).not.toContain('ASR 引擎');
      expect(html).not.toContain('link-stale-settings');
    }
  });
});

describe('refreshVideosAfterRun', () => {
  it('reloads the video list after a terminal run status', async () => {
    const { loadVideos } = await import('../sidebar.js');
    vi.mocked(loadVideos).mockClear();
    await refreshVideosAfterRun();
    expect(loadVideos).toHaveBeenCalled();
  });
});

describe('run completion notifications', () => {
  it('registers a persistent local transcribe failure notification once per project', async () => {
    const { api } = await import('../api.js');
    const { registerNotification } = await import('../notification-center.js');
    vi.mocked(api).mockReset();
    vi.mocked(registerNotification).mockClear();
    api.mockImplementation((method, url) => {
      if (url === '/api/whisper/check') {
        return Promise.resolve({ engine: 'local', ready: false, model_cached: false });
      }
      return Promise.resolve({ files: { GL010684: { transcribe: 'error' } } });
    });
    state.currentProjectDir = 'G:/projects/trip';
    document.body.innerHTML = '<div id="run-progress"></div><div id="run-state-container"></div>';

    await _handleRunStatus({ status: 'done', steps: ['transcribe'] });

    expect(api).toHaveBeenCalledWith('GET', '/api/processing-state');
    const notification = registerNotification.mock.calls.map(([payload]) => payload)
      .find(payload => payload.sourceId === 'transcribe-failure');
    expect(notification).toMatchObject({
      title: '部分视频转录失败',
      severity: 'error',
      dedupeKey: 'pipeline:transcribe-failure:G:/projects/trip',
    });
    expect(notification.message).toContain('faster-whisper');
    expect(notification.message).not.toContain('API Key');
  });

  it('uses cloud credential guidance after a cloud transcription failure', async () => {
    const { api } = await import('../api.js');
    const { registerNotification } = await import('../notification-center.js');
    vi.mocked(api).mockReset();
    vi.mocked(registerNotification).mockClear();
    api.mockImplementation((method, url) => {
      if (url === '/api/whisper/check') {
        return Promise.resolve({
          engine: 'aliyun',
          engine_display_name: '阿里云 Paraformer',
          ready: false,
          readiness_error: '阿里云 ASR 未配置 DASHSCOPE_API_KEY。',
          requires_local_model: false,
        });
      }
      return Promise.resolve({ files: { GL010684: { transcribe: 'error' } } });
    });
    state.currentProjectDir = 'G:/projects/trip';
    document.body.innerHTML = '<div id="run-progress"></div><div id="run-state-container"></div>';

    await _handleRunStatus({ status: 'done', steps: ['transcribe'] });

    const notification = registerNotification.mock.calls.map(([payload]) => payload)
      .find(payload => payload.sourceId === 'transcribe-failure');
    expect(notification.message).toContain('阿里云 Paraformer');
    expect(notification.message).toContain('API Key');
    expect(notification.message).not.toContain('Whisper 模型未下载');
    expect(document.getElementById('run-transcribe-warn').textContent).toContain('编辑 .env 文件');
  });
});

describe('skipped diagnostics', () => {
  it('builds inferred skipped reasons from processing state', () => {
    const diagnostics = buildSkippedDiagnostics({
      steps: ['compress', 'transcribe', 'analyze', 'voiceover', 'plan', 'label'],
      files: {
        GL010683: { compress: 'done', analyze: 'skipped', voiceover: null },
        GL010684: { compress: 'skipped', analyze: 'done', transcribe: 'error' },
      },
    });

    expect(diagnostics).toHaveLength(2);
    expect(diagnostics[0]).toMatchObject({
      file: 'GL010683',
      step: 'analyze',
      label: '分析',
    });
    expect(diagnostics[0].reason).toContain('分析 JSON');
    expect(diagnostics[1]).toMatchObject({
      file: 'GL010684',
      step: 'compress',
      label: '压缩',
    });
  });

  it('renders skipped diagnostics and escapes dynamic strings', () => {
    const html = renderSkippedDiagnosticsHtml([
      {
        file: '<video>',
        step: 'label',
        label: '<b>标号</b>',
        reason: '找不到 <output>',
      },
    ]);

    expect(html).toContain('为什么被跳过');
    expect(html).toContain('&lt;video&gt;');
    expect(html).toContain('&lt;b&gt;标号&lt;/b&gt;');
    expect(html).toContain('找不到 &lt;output&gt;');
    expect(html).not.toContain('<video>');
    expect(html).not.toContain('<b>标号</b>');
  });

  it('renders an empty skipped diagnostics state', () => {
    const html = renderSkippedDiagnosticsHtml([]);

    expect(html).toContain('当前没有 skipped 记录');
  });
});
