import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../utils.js', async () => {
  const actual = await vi.importActual('../utils.js');
  return { ...actual, setStatus: vi.fn() };
});

beforeEach(async () => {
  vi.resetModules();
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
});
