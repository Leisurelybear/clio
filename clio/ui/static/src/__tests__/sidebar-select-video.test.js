import { beforeEach, describe, expect, it, vi } from 'vitest';

const elements = new Map();

function element(id) {
  if (!elements.has(id)) {
    elements.set(id, {
      id,
      classList: { add() {}, remove() {}, toggle() {} },
      currentTime: 0,
      duration: 0,
      play: vi.fn(() => Promise.resolve()),
      removeAttribute: vi.fn(),
      textContent: '',
    });
  }
  return elements.get(id);
}

vi.mock('../api.js', () => {
  const api = vi.fn();
  return {
    api,
    apiWithMeta: vi.fn(async (...args) => ({ data: await api(...args), version: null })),
    icon: vi.fn(() => ''),
  };
});
vi.mock('../utils.js', () => ({
  $: vi.fn((id) => element(id)),
  $$: vi.fn(() => []),
  setStatus: vi.fn(),
  fmtTime: vi.fn((value) => String(value)),
  updateSidebarDay: vi.fn(),
  updateEntityUI: vi.fn(),
  clearDirty: vi.fn(),
}));
vi.mock('../viewer.js', () => ({ playVideoSegment: vi.fn(), stopPreview: vi.fn() }));
vi.mock('../waveform.js', () => ({ loadWaveformForCurrentVideo: vi.fn() }));
vi.mock('../sidebar-rerun.js', () => ({ showRerunProgress: vi.fn(), hideRerunProgress: vi.fn() }));
vi.mock('../sidebar-video-manage.js', () => ({ openVideoManager: vi.fn() }));
vi.mock('../sidebar-data.js', () => ({
  loadProjects: vi.fn(), loadConfig: vi.fn(), loadFfmpegDeps: vi.fn(), loadPlans: vi.fn(),
  loadProject: vi.fn(), loadVideos: vi.fn(), saveProject: vi.fn(), updateSelectBtnVisibility: vi.fn(),
  renderSteps: vi.fn(), renderVideoList: vi.fn(),
}));
vi.mock('../select-btn.js', () => ({ selectVideosButtonHtml: vi.fn(() => '') }));
vi.mock('../editor.js', () => ({ renderActiveTab: vi.fn() }));

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

describe('selectVideo request ordering', () => {
  beforeEach(async () => {
    elements.clear();
    const { state } = await import('../state.js');
    Object.assign(state, {
      currentProjectName: null,
      currentVideo: null,
      currentEntity: 'video',
      currentDay: 'day1',
      dirty: false,
      plan: {},
      artifactVersions: { plan: {}, texts: {}, voiceover: {} },
      previewActive: false,
      source: 'compressed',
      texts: null,
      voiceover: null,
      transcript: null,
      availablePlans: [],
      videos: [
        { file: 'a.mp4', text_json: 'a.json' },
        { file: 'b.mp4', text_json: 'b.json' },
      ],
    });
  });

  it('ignores a slower response from the previously selected video', async () => {
    const a = deferred();
    const b = deferred();
    const { api } = await import('../api.js');
    api.mockImplementation((_method, url) => url.includes('a.json') ? a.promise : b.promise);
    const { selectVideo } = await import('../sidebar.js');
    const { state } = await import('../state.js');

    const first = selectVideo('a.mp4');
    const second = selectVideo('b.mp4');
    b.resolve({ title: 'B' });
    await second;
    expect(state.texts).toEqual({ title: 'B' });

    a.resolve({ title: 'A' });
    await first;
    expect(state.currentVideo).toBe('b.mp4');
    expect(state.texts).toEqual({ title: 'B' });
  });

  it('restores the requested time and playback after metadata loads', async () => {
    const { state } = await import('../state.js');
    state.source = 'original';
    state.videos = [{ file: 'a.mp4', offset_sec: 120 }];

    const { selectVideo } = await import('../sidebar.js');
    await selectVideo('a.mp4', { seekSec: 135, play: true });

    const player = element('player');
    player.duration = 300;
    player.onloadedmetadata();

    expect(player.currentTime).toBe(135);
    expect(player.play).toHaveBeenCalledOnce();
  });

  it('does not request a plan when the current day has none', async () => {
    const { state } = await import('../state.js');
    const { api } = await import('../api.js');
    const { setStatus } = await import('../utils.js');
    const { selectVideo } = await import('../sidebar.js');
    state.plan = null;
    state.availablePlans = [];
    api.mockReset();
    api.mockResolvedValue({});
    setStatus.mockClear();

    await selectVideo('a.mp4');

    expect(api.mock.calls.some(([, url]) => String(url).includes('/api/plan'))).toBe(false);
    expect(setStatus).not.toHaveBeenCalled();
  });

  it('loads the plan listed for the current day', async () => {
    const { state } = await import('../state.js');
    const { api } = await import('../api.js');
    const { selectVideo } = await import('../sidebar.js');
    const plan = { day: 'day1', sequence: [] };
    state.plan = null;
    state.availablePlans = [{ day_label: 'day1' }];
    api.mockReset();
    api.mockImplementation((_method, url) => (
      String(url).includes('/api/plan') ? Promise.resolve(plan) : Promise.resolve({})
    ));

    await selectVideo('a.mp4');

    expect(state.plan).toEqual(plan);
  });

  it('treats a listed but missing plan file as absent, not an error', async () => {
    const { state } = await import('../state.js');
    const { api } = await import('../api.js');
    const { setStatus } = await import('../utils.js');
    const { selectVideo } = await import('../sidebar.js');
    state.plan = null;
    state.availablePlans = [{ day_label: 'day1' }];
    api.mockReset();
    api.mockImplementation((_method, url) => {
      if (String(url).includes('/api/plan')) {
        const err = new Error('HTTP 404: plan not found');
        err.status = 404;
        return Promise.reject(err);
      }
      return Promise.resolve({});
    });
    setStatus.mockClear();

    await selectVideo('a.mp4');

    expect(state.plan).toBeNull();
    expect(setStatus).not.toHaveBeenCalled();
  });

  it('still reports non-404 plan failures', async () => {
    const { state } = await import('../state.js');
    const { api } = await import('../api.js');
    const { setStatus } = await import('../utils.js');
    const { selectVideo } = await import('../sidebar.js');
    state.plan = null;
    state.availablePlans = [{ day_label: 'day1' }];
    api.mockReset();
    api.mockImplementation((_method, url) => {
      if (String(url).includes('/api/plan')) {
        const err = new Error('HTTP 500: server error');
        err.status = 500;
        return Promise.reject(err);
      }
      return Promise.resolve({});
    });
    setStatus.mockClear();

    await selectVideo('a.mp4');

    const { t } = await import('../i18n.js');
    expect(setStatus).toHaveBeenCalledWith(
      t('sidebar.artifact_load_failed', { label: 'plan', detail: 'HTTP 500: server error' }),
      'err',
    );
  });
});
