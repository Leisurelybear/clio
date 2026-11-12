import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({ api: vi.fn() }));
vi.mock('../toast.js', () => ({ addToast: vi.fn() }));
vi.mock('../confirm.js', () => ({ confirmDialog: vi.fn(async () => true) }));
vi.mock('../runtime-warnings.js', () => ({ updateRuntimeWarnings: vi.fn() }));
vi.mock('../ffmpeg-install.js', () => ({ openFfmpegInstallDialog: vi.fn() }));

beforeEach(async () => {
  vi.resetModules();
  const { seedCatalogs } = await import('./catalogs.js');
  seedCatalogs();
});

describe('runtime warning actions', () => {
  it('refreshes dependency probes and forwards their state to the banner', async () => {
    const { api } = await import('../api.js');
    const { updateRuntimeWarnings } = await import('../runtime-warnings.js');
    const { state } = await import('../state.js');
    api.mockImplementation(async (_method, url) => {
      if (url === '/api/deps/keys') return { missing: ['gemini'], unbound_tasks: ['plan'] };
      if (url === '/api/cut/orphaned-backups') return { items: [{ file: 'clip.mp4' }] };
      return {};
    });
    state.config = { project_dir: 'D:/project' };
    state.deps = { ffmpeg: true };

    const { createRuntimeWarningsController } = await import('../runtime-warning-actions.js');
    const controller = createRuntimeWarningsController({
      loadFfmpegDeps: vi.fn(),
      renderVideoList: vi.fn(),
      selectConfig: vi.fn(),
    });
    await controller.refreshRuntimeWarningsBanner();

    expect(updateRuntimeWarnings).toHaveBeenCalledWith(state.config, expect.objectContaining({
      orphanedCutBackups: [{ file: 'clip.mp4' }],
      ffmpegDeps: state.deps,
      missingKeys: ['gemini'],
      unboundTasks: ['plan'],
    }));
  });

  it('re-probes ffmpeg and re-renders rows before returning dependencies', async () => {
    const { api } = await import('../api.js');
    const { state } = await import('../state.js');
    api.mockResolvedValue({ missing: [], unbound_tasks: [], items: [] });
    state.deps = { ffmpeg: true };
    const loadFfmpegDeps = vi.fn(async () => { state.deps = { ffmpeg: false }; });
    const renderVideoList = vi.fn();
    const { createRuntimeWarningsController } = await import('../runtime-warning-actions.js');
    const controller = createRuntimeWarningsController({
      loadFfmpegDeps,
      renderVideoList,
      selectConfig: vi.fn(),
    });

    await expect(controller.refreshFfmpegDepsUi()).resolves.toEqual({ ffmpeg: false });
    expect(loadFfmpegDeps).toHaveBeenCalledOnce();
    expect(renderVideoList).toHaveBeenCalledOnce();
  });

  it('opens the managed ffmpeg installer from a runtime warning action', async () => {
    const { openFfmpegInstallDialog } = await import('../ffmpeg-install.js');
    const { createRuntimeWarningsController } = await import('../runtime-warning-actions.js');
    const controller = createRuntimeWarningsController({
      loadFfmpegDeps: vi.fn(),
      renderVideoList: vi.fn(),
      selectConfig: vi.fn(),
    });

    await controller.handleRuntimeWarningAction('show-ffmpeg-help');

    expect(openFfmpegInstallDialog).toHaveBeenCalledWith({
      refreshDeps: controller.refreshFfmpegDepsUi,
    });
  });
});
