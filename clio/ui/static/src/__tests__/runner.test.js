import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  buildSkippedDiagnostics,
  renderRunPreviewHtml,
  renderSkippedDiagnosticsHtml,
  collectRunOptions,
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

describe('renderRunPreviewHtml', () => {
  it('renders totals and per-step counts', () => {
    const html = renderRunPreviewHtml({
      input: { mode: 'directory', path: 'D:/trip/videos', count: 3 },
      totals: { selected_steps: 2, will_run: 4, will_skip: 1, warnings: 0 },
      steps: [
        { name: 'compress', label: '压缩视频', total: 3, will_run: 2, will_skip: 1, warnings: [] },
        { name: 'analyze', label: 'AI 分析', total: 2, will_run: 2, will_skip: 0, warnings: [] },
      ],
    });

    expect(html).toContain('运行预览');
    expect(html).toContain('D:/trip/videos');
    expect(html).toContain('压缩视频');
    expect(html).toContain('AI 分析');
    expect(html).toContain('待执行');
    expect(html).toContain('4');
    expect(html).toContain('跳过');
    expect(html).toContain('1');
  });

  it('renders warnings and escapes dynamic strings', () => {
    const html = renderRunPreviewHtml({
      input: { mode: 'directory', path: '<script>', count: 0 },
      totals: { selected_steps: 1, will_run: 0, will_skip: 0, warnings: 1 },
      steps: [
        {
          name: 'unknown',
          label: '<b>bad</b>',
          total: 0,
          will_run: 0,
          will_skip: 0,
          warnings: ['未知步骤：<x>'],
        },
      ],
    });

    expect(html).toContain('&lt;script&gt;');
    expect(html).toContain('&lt;b&gt;bad&lt;/b&gt;');
    expect(html).toContain('未知步骤：&lt;x&gt;');
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('<b>bad</b>');
  });

  it('renders a neutral state without preview data', () => {
    expect(renderRunPreviewHtml(null)).toContain('选择步骤后显示预览');
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
      <input class="run-step-cb" data-step="plan" type="checkbox">
      <input id="run-day" value="day2">
      <input id="run-use-transcripts" type="checkbox" checked>
      <input id="run-overwrite" type="checkbox">
      <textarea id="run-context-override">[analyze] focus food</textarea>
    `;
  });

  it('collects checked steps and options from the run form', () => {
    const opts = collectRunOptions();
    expect(opts.steps).toEqual(['compress', 'analyze']);
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

describe('staleWarningHtml', () => {
  it('shows the Whisper model hint only for the transcribe phase', () => {
    const html = staleWarningHtml('transcribe');
    expect(html).toContain('stale-warn');
    expect(html).toContain('Whisper 模型管理');
    expect(html).toContain('link-stale-settings');
  });

  it('omits the Whisper hint for non-transcribe phases', () => {
    for (const phase of ['compress', 'analyze', 'voiceover', 'plan', 'label', '启动', '']) {
      const html = staleWarningHtml(phase);
      expect(html).toContain('stale-warn');
      expect(html).toContain('进度长时间未更新');
      expect(html).not.toContain('Whisper');
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
  it('registers a persistent transcribe failure notification once per project', async () => {
    const { api } = await import('../api.js');
    const { registerNotification } = await import('../notification-center.js');
    vi.mocked(api).mockReset();
    vi.mocked(registerNotification).mockClear();
    api.mockResolvedValue({ files: { GL010684: { transcribe: 'error' } } });
    state.currentProjectDir = 'G:/projects/trip';
    document.body.innerHTML = '<div id="run-progress"></div><div id="run-state-container"></div>';

    await _handleRunStatus({ status: 'done', steps: ['transcribe'] });

    expect(api).toHaveBeenCalledWith('GET', '/api/processing-state');
    const notification = registerNotification.mock.calls.map(([payload]) => payload)
      .find(payload => payload.sourceId === 'transcribe-model-missing');
    expect(notification).toMatchObject({
      title: '部分视频转录失败',
      severity: 'error',
      dedupeKey: 'pipeline:transcribe-model-missing:G:/projects/trip',
    });
  });
});

describe('skipped diagnostics', () => {
  it('builds inferred skipped reasons from processing state', () => {
    const diagnostics = buildSkippedDiagnostics({
      steps: ['compress', 'analyze', 'voiceover', 'transcribe', 'plan', 'label'],
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
