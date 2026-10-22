import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({
  api: vi.fn(() => Promise.resolve({})),
  icon: vi.fn(() => ''),
}));

vi.mock('../sidebar.js', () => ({
  loadVideos: vi.fn(() => Promise.resolve()),
  loadPlans: vi.fn(() => Promise.resolve()),
  renderVideoList: vi.fn(),
  saveProject: vi.fn(() => Promise.resolve()),
  selectPlan: vi.fn(() => Promise.resolve()),
  selectTasks: vi.fn(() => Promise.resolve()),
  selectConfig: vi.fn(() => Promise.resolve()),
  setSource: vi.fn(() => Promise.resolve()),
  selectVideo: vi.fn(() => Promise.resolve()),
}));

vi.mock('../task-center.js', () => ({
  subscribeTaskEvents: vi.fn(() => () => {}),
  fetchTask: vi.fn(() => Promise.resolve(null)),
}));

vi.mock('../notification-center.js', () => ({
  registerNotification: vi.fn(() => Promise.resolve(null)),
}));

import { applyLocale } from '../i18n.js';
import {
  buildSkippedDiagnostics,
  getRunButtonText,
  renderRun,
  renderRunPreviewHtml,
  staleWarningHtml,
} from '../runner.js';

describe('run pane localization', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="tab-run"></div><div id="run-state-container"></div>';
  });

  afterEach(() => applyLocale('zh-CN'));

  it('localizes the run helpers in the active locale', () => {
    applyLocale('en');
    expect(getRunButtonText()).toContain('Run selected steps');
    expect(renderRunPreviewHtml(null)).toContain('Pick steps to see a preview');
    expect(staleWarningHtml('transcribe')).toContain('ASR engine');
    const diagnostics = buildSkippedDiagnostics({
      steps: ['analyze'],
      files: { GL010683: { analyze: 'skipped' } },
    });
    expect(diagnostics[0].label).toBe('Analyze');
    expect(diagnostics[0].reason).toContain('analysis JSON');
  });

  it('re-renders the run pane when the locale changes', async () => {
    const { state } = await import('../state.js');
    state.currentEntity = 'run';
    state.currentDay = 'day1';

    renderRun();
    expect(document.getElementById('tab-run').innerHTML).toContain('\u8fd0\u884c\u6d41\u6c34\u7ebf');

    applyLocale('en');
    expect(document.getElementById('tab-run').innerHTML).toContain('Run pipeline');

    applyLocale('zh-CN');
    expect(document.getElementById('tab-run').innerHTML).toContain('\u8fd0\u884c\u6d41\u6c34\u7ebf');
  });
});
