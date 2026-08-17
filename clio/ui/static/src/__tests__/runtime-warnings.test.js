import { beforeEach, describe, it, expect } from 'vitest';
import { buildRuntimeWarnings, renderRuntimeWarnings, warningOccurrences } from '../runtime-warnings.js';
import { state } from '../state.js';

beforeEach(() => {
  localStorage.clear();
  state.currentProjectDir = 'C:/projects/demo';
});

describe('buildRuntimeWarnings', () => {
  it('warns when debug prompt printing is enabled', () => {
    const warnings = buildRuntimeWarnings({
      config: { ai: { debug_print_prompt: true } },
      hostname: '127.0.0.1',
      hasToken: true,
    });

    expect(warnings).toEqual([
      expect.objectContaining({
        id: 'debug-prompt',
        level: 'warning',
      }),
    ]);
    expect(warnings[0].text).toContain('debug_print_prompt');
  });

  it('does not warn for local hosts when debug prompt printing is disabled', () => {
    const warnings = buildRuntimeWarnings({
      config: { ai: { debug_print_prompt: false } },
      hostname: 'localhost',
      hasToken: false,
    });

    expect(warnings).toEqual([]);
  });

  it('warns when the UI is opened through a LAN host', () => {
    const warnings = buildRuntimeWarnings({
      config: { ai: { debug_print_prompt: false } },
      hostname: '192.168.1.25',
      hasToken: true,
    });

    expect(warnings).toEqual([
      expect.objectContaining({
        id: 'lan-host',
        level: 'warning',
      }),
    ]);
  });

  it('escalates LAN warning when no token is present', () => {
    const warnings = buildRuntimeWarnings({
      config: { ai: { debug_print_prompt: false } },
      hostname: '10.0.0.8',
      hasToken: false,
    });

    expect(warnings).toEqual([
      expect.objectContaining({
        id: 'lan-no-token',
        level: 'danger',
      }),
    ]);
  });

  it('warns when orphaned cut backups exist', () => {
    const warnings = buildRuntimeWarnings({
      config: {},
      hostname: '127.0.0.1',
      hasToken: true,
      orphanedCutBackups: [{ name: 'a.mp4', bak: '/x/a.mp4.clio_bak' }],
    });
    expect(warnings.some((w) => w.id === 'cut-orphaned-bak')).toBe(true);
    const w = warnings.find((w) => w.id === 'cut-orphaned-bak');
    expect(w.action?.id).toBe('restore-cut-backups');
    expect(w.text).toMatch(/clio_bak|备份/);
  });

  it('warns when ffmpeg deps are missing', () => {
    const warnings = buildRuntimeWarnings({
      config: {},
      hostname: '127.0.0.1',
      hasToken: true,
      ffmpegDeps: {
        ok: false,
        missing: ['ffmpeg', 'ffprobe'],
        detail: '未找到 ffmpeg、ffprobe。请运行 setup.ps1。',
      },
    });
    const w = warnings.find((x) => x.id === 'ffmpeg-missing');
    expect(w).toBeTruthy();
    expect(w.level).toBe('warning');
    expect(w.text).toMatch(/ffmpeg|setup/i);
    expect(w.action?.id).toBe('show-ffmpeg-help');
  });

  it('warns with go-settings action when providers lack API keys', () => {
    const warnings = buildRuntimeWarnings({
      config: {},
      hostname: '127.0.0.1',
      hasToken: true,
      missingKeys: [{ provider: 'gemini', api_key_env: 'GEMINI_API_KEY', detail: '缺少' }],
    });
    const w = warnings.find((x) => x.id === 'deps-keys-missing');
    expect(w).toBeTruthy();
    expect(w.level).toBe('danger');
    expect(w.action?.id).toBe('go-settings-keys');
    expect(w.text).toMatch(/gemini/);
  });

  it('does not warn when missingKeys is empty', () => {
    const warnings = buildRuntimeWarnings({
      config: {},
      hostname: '127.0.0.1',
      hasToken: true,
      missingKeys: [],
    });
    expect(warnings.some((x) => x.id === 'deps-keys-missing')).toBe(false);
  });

  it('does not warn when ffmpeg deps ok', () => {
    const warnings = buildRuntimeWarnings({
      config: {},
      hostname: '127.0.0.1',
      hasToken: true,
      ffmpegDeps: { ok: true, missing: [], detail: '' },
    });
    expect(warnings.some((x) => x.id === 'ffmpeg-missing')).toBe(false);
  });
});

describe('renderRuntimeWarnings', () => {
  it('renders warnings as text content', () => {
    const container = document.createElement('div');
    renderRuntimeWarnings(container, [
      { id: 'x', level: 'warning', text: '<script>alert(1)</script>' },
    ]);

    expect(container.querySelector('.runtime-warning')).not.toBeNull();
    expect(container.textContent).toContain('<script>alert(1)</script>');
    expect(container.innerHTML).not.toContain('<script>');
  });

  it('renders action button and invokes handler', () => {
    const container = document.createElement('div');
    let called = null;
    renderRuntimeWarnings(
      container,
      [
        {
          id: 'cut-orphaned-bak',
          level: 'warning',
          text: 'orphans',
          action: { id: 'restore-cut-backups', label: '恢复旧文件' },
        },
      ],
      { onAction: (id) => { called = id; } },
    );
    const btn = container.querySelector('.runtime-warning-action');
    expect(btn).not.toBeNull();
    expect(btn.textContent).toBe('恢复旧文件');
    btn.click();
    expect(called).toBe('restore-cut-backups');
  });

  it('hides the container when there are no warnings', () => {
    const container = document.createElement('div');
    renderRuntimeWarnings(container, []);

    expect(container.hidden).toBe(true);
    expect(container.children.length).toBe(0);
  });
});

describe('warningOccurrences', () => {
  it('increments only after a warning disappears and returns', () => {
    const warning = { id: 'ffmpeg-missing', text: 'ffmpeg missing' };

    expect(warningOccurrences([warning]).get(warning.id)).toBe(1);
    expect(warningOccurrences([warning]).get(warning.id)).toBe(1);
    warningOccurrences([]);
    expect(warningOccurrences([warning]).get(warning.id)).toBe(2);
  });

  it('keeps occurrences independent for same-named projects', () => {
    const warning = { id: 'cut-orphaned-bak', text: 'backup' };
    expect(warningOccurrences([warning]).get(warning.id)).toBe(1);
    state.currentProjectDir = 'C:/projects/other';
    expect(warningOccurrences([warning]).get(warning.id)).toBe(1);
  });

  it('recovers from valid JSON with the wrong storage shape', () => {
    localStorage.setItem('clio.runtime-warning-state.v1', 'null');

    expect(warningOccurrences([{ id: 'ffmpeg-missing', text: 'missing' }]).get('ffmpeg-missing')).toBe(1);
  });
});
