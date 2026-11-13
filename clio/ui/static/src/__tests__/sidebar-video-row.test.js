import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({
  api: vi.fn(),
  icon: vi.fn(() => ''),
}));

vi.mock('../confirm.js', () => ({
  confirmDialog: vi.fn(async () => true),
}));

vi.mock('../sidebar-rerun.js', () => ({
  showRerunProgress: vi.fn(),
  hideRerunProgress: vi.fn(),
}));

vi.mock('../utils.js', async () => {
  const actual = await vi.importActual('../utils.js');
  return { ...actual, setStatus: vi.fn() };
});

beforeEach(async () => {
  vi.resetModules();
  document.body.innerHTML = '';
  const { seedCatalogs } = await import('./catalogs.js');
  seedCatalogs();
  const { state } = await import('../state.js');
  Object.assign(state, {
    source: 'compressed',
    currentVideo: null,
    selectionMode: false,
    selectedFiles: [],
    deps: {},
  });
});

async function openVideoMenu(v = { file: 'clip.mp4', match: { abs_path: 'D:/videos/clip.mp4' } }) {
  const { renderVideoItem } = await import('../sidebar-video-row.js');
  const li = renderVideoItem(v, {
    relinkVideo: vi.fn(),
    removeVideoFromProject: vi.fn(),
    rerenderVideoList: vi.fn(),
  });
  li.querySelector('.menu-btn').click();
  const action = document.body.querySelector('[data-action="voiceover"]');
  expect(action).not.toBeNull();
  return action;
}

describe('renderVideoItem', () => {
  it('opens relink when an offline row is clicked', async () => {
    const { renderVideoItem } = await import('../sidebar-video-row.js');
    const relinkVideo = vi.fn();
    const li = renderVideoItem(
      { file: 'offline.mp4', abs_path: 'D:/videos/offline.mp4', missing: true },
      { relinkVideo, removeVideoFromProject: vi.fn(), rerenderVideoList: vi.fn() },
    );

    expect(li.classList.contains('missing')).toBe(true);
    li.click();
    expect(relinkVideo).toHaveBeenCalledWith('offline.mp4', 'D:/videos/offline.mp4');
  });

  it('updates shared selection state and requests a rerender', async () => {
    const { state } = await import('../state.js');
    const { renderVideoItem } = await import('../sidebar-video-row.js');
    const rerenderVideoList = vi.fn();
    state.selectionMode = true;
    const li = renderVideoItem(
      { file: 'clip.mp4' },
      { relinkVideo: vi.fn(), removeVideoFromProject: vi.fn(), rerenderVideoList },
    );

    const checkbox = li.querySelector('.video-checkbox');
    checkbox.checked = true;
    checkbox.dispatchEvent(new Event('change', { bubbles: true }));

    expect(state.selectedFiles).toEqual(['clip.mp4']);
    expect(rerenderVideoList).toHaveBeenCalledOnce();
  });

  it('confirms and retries a rerun when edited artifacts require overwrite confirmation', async () => {
    const { api } = await import('../api.js');
    const { confirmDialog } = await import('../confirm.js');
    vi.mocked(api).mockReset();
    vi.mocked(confirmDialog).mockReset();
    const conflict = Object.assign(new Error('conflict'), {
      status: 409,
      body: {
        code: 'artifact_overwrite_confirmation_required',
        items: ['001_A_voiceover.json'],
        error: 'conflict',
      },
    });
    vi.mocked(api)
      .mockRejectedValueOnce(conflict)
      .mockResolvedValueOnce({ ok: true, task_id: 'task-1' });
    vi.mocked(confirmDialog).mockResolvedValueOnce(true);

    const action = await openVideoMenu();
    action.click();

    await vi.waitFor(() => expect(api).toHaveBeenCalledTimes(2));
    expect(confirmDialog).toHaveBeenCalledWith(
      expect.stringContaining('001_A_voiceover.json'),
      { danger: true },
    );
    expect(api.mock.calls[0][2]).not.toHaveProperty('confirm_artifact_overwrite');
    expect(api).toHaveBeenNthCalledWith(
      2,
      'POST',
      '/api/rerun',
      expect.objectContaining({
        video: 'clip.mp4',
        task: 'voiceover',
        confirm_artifact_overwrite: true,
      }),
    );
  });

  it('does not retry a rerun when overwrite confirmation is cancelled', async () => {
    const { api } = await import('../api.js');
    const { confirmDialog } = await import('../confirm.js');
    const { setStatus } = await import('../utils.js');
    const { t } = await import('../i18n.js');
    vi.mocked(api).mockReset();
    vi.mocked(confirmDialog).mockReset();
    vi.mocked(setStatus).mockClear();
    const conflict = Object.assign(new Error('conflict'), {
      status: 409,
      body: {
        code: 'artifact_overwrite_confirmation_required',
        items: ['001_A_voiceover.json'],
        error: 'conflict',
      },
    });
    vi.mocked(api).mockRejectedValueOnce(conflict);
    vi.mocked(confirmDialog).mockResolvedValueOnce(false);

    const action = await openVideoMenu();
    action.click();

    await vi.waitFor(() => expect(confirmDialog).toHaveBeenCalledOnce());
    expect(api).toHaveBeenCalledOnce();
    expect(setStatus).toHaveBeenLastCalledWith(
      t('sidebar.rerun_cancelled'),
      'warn',
      { persist: false },
    );
  });
});
