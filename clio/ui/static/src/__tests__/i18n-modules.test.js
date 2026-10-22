import { afterEach, describe, expect, it } from 'vitest';
import { applyLocale } from '../i18n.js';
import { renderEmptyArtifactHtml } from '../empty-states.js';
import { kindLabel, statusLabel } from '../task-center.js';
import { manualInstallGuidance, taskStatusText } from '../ffmpeg-install.js';
import { buildRuntimeWarnings } from '../runtime-warnings.js';
import { historyStatusText } from '../logs-history.js';
import { STAGE_CELLS, stageLabel } from '../sidebar-video-filter.js';
import { selectVideosButtonLabel } from '../select-btn.js';
import { pickerTitle } from '../file-browser.js';
import { transcriptionEngineLabel, transcriptionReadinessPrompt } from '../transcription-status.js';
import { buildVideoMenuItems } from '../video-menu.js';
import { renderSubtitleSettingsPanel } from '../subtitle-settings.js';

afterEach(() => applyLocale('zh-CN'));

describe('localized module copy', () => {
  it('renders empty states in the active locale', () => {
    applyLocale('en');
    const html = renderEmptyArtifactHtml('texts');
    expect(html).toContain('No analysis result');
    expect(html).toContain('Run the pipeline');
  });

  it('labels task status and kind in the active locale', () => {
    applyLocale('en');
    expect(statusLabel('running')).toBe('Running');
    expect(kindLabel('cut_export')).toBe('Cut export');
  });

  it('labels ffmpeg task states and guidance in the active locale', () => {
    applyLocale('en');
    expect(taskStatusText({ status: 'cancelling' })).toBe('Cancelling...');
    expect(manualInstallGuidance('linux').steps[0]).toContain('apt install ffmpeg');
    expect(manualInstallGuidance('linux').downloadLabel).toBe('Open manual download page');
  });

  it('builds runtime warnings in the active locale', () => {
    applyLocale('en');
    const warnings = buildRuntimeWarnings({
      config: { ai: { debug_print_prompt: true } },
      hostname: '127.0.0.1',
      hasToken: true,
    });
    expect(warnings[0].text).toContain('debug_print_prompt');
    expect(warnings[0].text).toMatch(/logs\/console/);
  });

  it('keeps zh-CN copy for the default locale', () => {
    applyLocale('zh-CN');
    expect(statusLabel('running')).toBe('\u8fd0\u884c\u4e2d');
    expect(renderEmptyArtifactHtml('texts')).toContain('\u6ca1\u6709\u5206\u6790\u7ed3\u679c');
  });

  it('localizes sidebar stage labels and the select button', () => {
    applyLocale('en');
    expect(stageLabel(STAGE_CELLS[0])).toBe('Compress');
    expect(selectVideosButtonLabel(true)).toBe('Deselect');
    expect(selectVideosButtonLabel(false)).toBe('Select videos');
    applyLocale('zh-CN');
    expect(stageLabel(STAGE_CELLS[0])).toBe('压缩');
    expect(selectVideosButtonLabel(true)).toBe('取消选择');
  });

  it('localizes the video menu and file picker copy', () => {
    applyLocale('en');
    expect(pickerTitle({ mode: 'folder' })).toBe('Select a folder');
    expect(pickerTitle({ multiple: true, kind: 'video' })).toBe('Select videos');
    const enItems = buildVideoMenuItems({ missing: false, file: 'a.mp4' }, 'compressed');
    expect(enItems.find((i) => i.action === 'transcribe').label).toBe('Transcribe audio');
    expect(enItems.find((i) => i.action === 'transcribe').title).toMatch(/cloud ASR engine/);
    applyLocale('zh-CN');
    expect(pickerTitle({ mode: 'folder' })).toBe('选择目录');
    const zhItems = buildVideoMenuItems({ missing: false, file: 'a.mp4' }, 'compressed');
    expect(zhItems.find((i) => i.action === 'transcribe').label).toBe('语音转录');
  });

  it('localizes transcription guidance and subtitle settings', () => {
    applyLocale('en');
    expect(transcriptionEngineLabel({ engine: 'local' })).toBe('Local faster-whisper');
    expect(transcriptionReadinessPrompt({
      engine: 'local', installed: false, frozen: false, model_cached: false,
    }).title).toBe('Local Whisper model not downloaded');
    const enPanel = document.createElement('div');
    renderSubtitleSettingsPanel(enPanel, { config: {} });
    expect(enPanel.textContent).toContain('Subtitle style');

    applyLocale('zh-CN');
    expect(transcriptionEngineLabel({ engine: 'local' })).toBe('本地 faster-whisper');
    const zhPanel = document.createElement('div');
    renderSubtitleSettingsPanel(zhPanel, { config: {} });
    expect(zhPanel.textContent).toContain('字幕样式');
  });

  it('localizes the historical log status line', () => {
    applyLocale('en');
    expect(historyStatusText({ name: '2026-07-20-22.log', logs: [1, 2], truncated: true }))
      .toBe('2026-07-20 22:00 \u00b7 2 lines \u00b7 Showing the end of the file only');
    applyLocale('zh-CN');
    expect(historyStatusText({ name: '2026-07-20-22.log', logs: [1, 2], truncated: true }))
      .toBe('2026-07-20 22:00 \u00b7 2 \u884c \u00b7 \u4ec5\u663e\u793a\u6587\u4ef6\u672b\u5c3e');
  });
});
