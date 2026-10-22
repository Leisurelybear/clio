import { afterEach, describe, expect, it } from 'vitest';
import { applyLocale } from '../i18n.js';
import { renderEmptyArtifactHtml } from '../empty-states.js';
import { kindLabel, statusLabel } from '../task-center.js';
import { manualInstallGuidance, taskStatusText } from '../ffmpeg-install.js';
import { buildRuntimeWarnings } from '../runtime-warnings.js';

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
});