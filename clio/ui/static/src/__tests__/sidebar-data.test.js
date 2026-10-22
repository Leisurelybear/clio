import { beforeEach, describe, it, expect, vi } from 'vitest';

vi.mock('../api.js', async () => {
  const actual = await vi.importActual('../api.js');
  return {
    ...actual,
    api: vi.fn(),
  };
});

function mockEl() {
  return {
    textContent: '',
    innerHTML: '',
    style: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    appendChild() {},
    addEventListener() {},
    removeEventListener() {},
    closest() { return null; },
  };
}

vi.mock('../utils.js', async () => {
  const actual = await vi.importActual('../utils.js');
  return {
    ...actual,
    setStatus: vi.fn(),
    $: vi.fn(() => mockEl()),
    $$: vi.fn().mockReturnValue([]),
    escapeHtml: actual.escapeHtml,
    updateProjectSidebar: vi.fn(),
  };
});

vi.mock('../runner.js', () => ({
  updateRunFilesBadge: vi.fn(),
}));

vi.mock('../sidebar-relink.js', () => ({
  openRelinkModal: vi.fn(),
  closeRelinkModal: vi.fn(),
}));

beforeEach(async () => {
  vi.resetModules();
  const { seedCatalogs } = await import('./catalogs.js');
  seedCatalogs();
});

describe('videoDisplayName', () => {
  it.each(['seg', 'part', 'pt', 'chunk', 'PART'])('strips legacy %s suffix when segment label exists', async alias => {
    const { videoDisplayName } = await import('../sidebar-data.js');

    expect(videoDisplayName({ file: `001_GX010456_${alias}07.mp4`, segment_label: '7/2' })).toBe(
      'GX010456.mp4 [seg 7/2]',
    );
  });

  it('keeps a natural part suffix when the backend marks the file as whole', async () => {
    const { videoDisplayName } = await import('../sidebar-data.js');

    expect(videoDisplayName({ file: '001_holiday_part01.mp4', segment_label: null })).toBe('holiday_part01.mp4');
  });

  it('appends offline state after formatting', async () => {
    const { videoDisplayName } = await import('../sidebar-data.js');

    expect(videoDisplayName({ file: '001_GL_part02.mov', segment_label: '2/3', missing: true })).toBe(
      'GL.mov [seg 2/3] (离线)',
    );
  });
});

describe('updateSelectBtnVisibility', () => {
  beforeEach(async () => {
    document.body.innerHTML = `
      <button id="btn-select-videos" style="display:none">取消选择</button>
      <ul id="video-list"></ul>
    `;
    const { state } = await import('../state.js');
    state.videos = [{ file: 'a.mp4', index: '001' }];
    state.currentEntity = 'run';
    state.selectionMode = true;
    state.selectedFiles = ['a.mp4'];
  });

  it('shows 取消选择 while on run with selection mode', async () => {
    const { updateSelectBtnVisibility } = await import('../sidebar-data.js');
    const { state } = await import('../state.js');
    updateSelectBtnVisibility();
    const btn = document.getElementById('btn-select-videos');
    expect(btn.style.display).toBe('flex');
    expect(btn.innerHTML).toContain('取消选择');
    expect(state.selectionMode).toBe(true);
  });

  it('clears selection and restores 选择视频 when leaving run', async () => {
    const { updateSelectBtnVisibility } = await import('../sidebar-data.js');
    const { state } = await import('../state.js');
    state.currentEntity = 'plan';
    // Simulate stale label left after prior selection mode
    const btn = document.getElementById('btn-select-videos');
    btn.innerHTML = '<span class="icon">✕</span> 取消选择';

    updateSelectBtnVisibility();

    expect(btn.style.display).toBe('none');
    expect(state.selectionMode).toBe(false);
    expect(state.selectedFiles).toEqual([]);
    expect(btn.innerHTML).toContain('选择视频');
    expect(btn.innerHTML).not.toContain('取消选择');
  });

  it('keeps 选择视频 when re-entering run after auto-clear', async () => {
    const { updateSelectBtnVisibility } = await import('../sidebar-data.js');
    const { state } = await import('../state.js');
    state.currentEntity = 'plan';
    updateSelectBtnVisibility();
    state.currentEntity = 'run';
    updateSelectBtnVisibility();
    const btn = document.getElementById('btn-select-videos');
    expect(btn.style.display).toBe('flex');
    expect(btn.innerHTML).toContain('选择视频');
    expect(state.selectionMode).toBe(false);
  });
});

describe('relinkVideo', () => {
  it('opens relink modal with old path instead of using prompt', async () => {
    const { openRelinkModal } = await import('../sidebar-relink.js');
    const { relinkVideo } = await import('../sidebar-data.js');
    await relinkVideo('video.mp4', 'D:/old/video.mp4');

    expect(openRelinkModal).toHaveBeenCalledWith({
      oldPath: 'D:/old/video.mp4',
      displayName: 'video.mp4',
    });
  });

  it('falls back to file name when absPath is not provided', async () => {
    const { openRelinkModal } = await import('../sidebar-relink.js');
    const { relinkVideo } = await import('../sidebar-data.js');
    await relinkVideo('video.mp4', null);

    expect(openRelinkModal).toHaveBeenCalledWith({
      oldPath: 'video.mp4',
      displayName: 'video.mp4',
    });
  });
});

describe('submitRelink', () => {
  it('calls api with correct params', async () => {
    const { api } = await import('../api.js');
    api.mockImplementation(async (method, url) => {
      if (method === 'PUT' && url === '/api/videos/relink') {
        return { ok: true, path: 'D:/new/video.mp4' };
      }
      if (method === 'GET' && String(url).startsWith('/api/videos')) {
        return { videos: [], groups: {} };
      }
      return {};
    });

    const { submitRelink } = await import('../sidebar-data.js');
    const r = await submitRelink('D:/old/video.mp4', 'D:/new/video.mp4');

    expect(api).toHaveBeenCalledWith('PUT', '/api/videos/relink', {
      old_path: 'D:/old/video.mp4',
      new_path: 'D:/new/video.mp4',
    });
    expect(r.ok).toBe(true);
  });

  it('shows error status when api returns error', async () => {
    const { api } = await import('../api.js');
    const { setStatus } = await import('../utils.js');
    api.mockResolvedValue({ ok: false, error: '文件不存在' });

    const { submitRelink } = await import('../sidebar-data.js');
    await submitRelink('D:/old/video.mp4', 'D:/new/video.mp4');

    expect(setStatus).toHaveBeenCalledWith('重新关联失败: 文件不存在', 'err');
  });

  it('propagates network errors from api', async () => {
    const { api } = await import('../api.js');
    api.mockRejectedValue(new Error('网络错误'));

    const { submitRelink } = await import('../sidebar-data.js');
    await expect(submitRelink('D:/old/video.mp4', 'D:/new/video.mp4')).rejects.toThrow('网络错误');
  });
});


describe('videoThumbHtml', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('renders icon placeholder when no cover_file', async () => {
    const { videoThumbHtml } = await import('../sidebar-data.js');
    const html = videoThumbHtml({ file: 'a.mp4' });
    expect(html).toContain('video-thumb');
    expect(html).not.toContain('has-cover');
    expect(html).not.toContain('/api/cover');
  });

  it('renders cover img with project params when cover_file set', async () => {
    const { state } = await import('../state.js');
    state.currentProjectName = 'France';
    state.currentProjectDir = 'D:/vlog/France';
    sessionStorage.setItem('api_token', 'tok123');

    const { videoThumbHtml } = await import('../sidebar-data.js');
    const html = videoThumbHtml({ cover_file: 'covers/001_title.jpg' });
    expect(html).toContain('has-cover');
    expect(html).toContain('/api/cover?');
    expect(html).toContain('file=001_title.jpg');
    expect(html).toContain('project=France');
    expect(html).toContain('token=tok123');
    expect(html).toContain('video-thumb-fallback');
  });
});

describe('videoLoadingStateHtml', () => {
  it('renders a spinner message and skeleton rows', async () => {
    const { videoLoadingStateHtml } = await import('../sidebar-data.js');
    const html = videoLoadingStateHtml('正在扫描压缩视频…', 0);
    expect(html).toContain('video-loading-state');
    expect(html).toContain('正在扫描压缩视频…');
    expect(html.match(/video-loading-row/g)).toHaveLength(3);
    expect(html).not.toContain('已等待');
  });

  it('shows elapsed seconds after the first second', async () => {
    const { videoLoadingStateHtml } = await import('../sidebar-data.js');
    const html = videoLoadingStateHtml('正在加载视频…', 3.2);
    expect(html).toContain('已等待 3 秒');
  });
});
