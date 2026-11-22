// Offline probes assert CURRENT defective behavior. These are review evidence,
// not regression expectations to retain when the defects are fixed.
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { state } from '../../../clio/ui/static/src/state.js';
import { api, apiWithMeta } from '../../../clio/ui/static/src/api.js';
import { selectPlan, selectVideo } from '../../../clio/ui/static/src/sidebar.js';
import { refineCurrentFile } from '../../../clio/ui/static/src/editor-refine.js';
import { saveEditorPlan } from '../../../clio/ui/static/src/editor-plan-persistence.js';

vi.mock('../../../clio/ui/static/src/api.js', () => ({ api: vi.fn(), apiWithMeta: vi.fn(), icon: () => '' }));
vi.mock('../../../clio/ui/static/src/utils.js', () => ({
  $: id => document.getElementById(id), $$: () => [],
  setStatus: vi.fn(), updateSidebarDay: vi.fn(), updateEntityUI: vi.fn(), fmtTime: String,
  clearDirty: () => { state.dirty = false; }, escapeHtml: String,
}));
vi.mock('../../../clio/ui/static/src/editor.js', () => ({
  renderActiveTab: vi.fn(), renderTexts: vi.fn(), renderVoiceover: vi.fn(),
}));
vi.mock('../../../clio/ui/static/src/editor-plan.js', () => ({ save: vi.fn() }));
vi.mock('../../../clio/ui/static/src/viewer.js', () => ({ stopPreview: vi.fn(), seekToGlobal: vi.fn() }));
vi.mock('../../../clio/ui/static/src/waveform.js', () => ({ loadWaveformForCurrentVideo: vi.fn() }));
vi.mock('../../../clio/ui/static/src/sidebar-rerun.js', () => ({ showRerunProgress: vi.fn(), hideRerunProgress: vi.fn() }));
vi.mock('../../../clio/ui/static/src/sidebar-video-manage.js', () => ({ openVideoManager: vi.fn() }));
vi.mock('../../../clio/ui/static/src/sidebar-data.js', () => ({
  loadProjects: vi.fn(), loadConfig: vi.fn(), loadFfmpegDeps: vi.fn(), loadPlans: vi.fn(),
  loadProject: vi.fn(), loadVideos: vi.fn(), saveProject: vi.fn(), updateSelectBtnVisibility: vi.fn(),
  renderVideoList: vi.fn(),
}));
vi.mock('../../../clio/ui/static/src/select-btn.js', () => ({ selectVideosButtonHtml: () => '' }));
vi.mock('../../../clio/ui/static/src/confirm.js', () => ({ confirmDialog: async () => true }));
vi.mock('../../../clio/ui/static/src/toast.js', () => ({ addToast: vi.fn() }));

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { resolve, promise };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.mockReset();
  apiWithMeta.mockReset();
  Object.assign(state, {
    currentEntity: 'video', currentVideo: 'a.mp4', currentTab: 'texts', currentDay: 'day1',
    currentProjectName: 'same-name', currentProjectDir: 'G:/projects/selected',
    dirty: false, texts: { title: 'A' }, voiceover: null, transcript: null,
    plan: {}, planPreviewMedia: null, availablePlans: [], source: 'compressed',
    previewActive: false, refining: null, _refineError: null,
    artifactVersions: { texts: {}, voiceover: {}, plan: {} },
    videos: [{ file: 'a.mp4', text_json: 'a.json' }, { file: 'b.mp4', text_json: 'b.json' }],
  });
  document.body.innerHTML = '<div id="player-pane"></div><video id="player"></video>'
    + '<span id="player-name"></span><span id="player-time"></span>'
    + '<textarea id="refine-context-texts"></textarea><p id="status"></p>';
});

describe('current boundary defects', () => {
  it('R04: late refine result for A replaces active B state', async () => {
    const result = deferred();
    api.mockReturnValue(result.promise);
    const pending = refineCurrentFile('texts');
    await vi.waitFor(() => expect(api).toHaveBeenCalled());
    state.currentVideo = 'b.mp4';
    state.texts = { title: 'B' };
    result.resolve({ ok: true, data: { title: 'AI A' }, version: 'a-version' });
    await pending;
    expect(state.currentVideo).toBe('b.mp4');
    expect(state.texts.title).toBe('AI A');
  });

  it('R05: slower day1 load overwrites day2 after navigation', async () => {
    const day1 = deferred();
    const day2 = deferred();
    api.mockResolvedValue(null);
    apiWithMeta.mockImplementation((_method, url) => url.endsWith('day1') ? day1.promise : day2.promise);
    const first = selectPlan('day1');
    const second = selectPlan('day2');
    day2.resolve({ data: { day_title: 'Day 2' }, version: 'day2-version' });
    await second;
    day1.resolve({ data: { day_title: 'Day 1' }, version: 'day1-version' });
    await first;
    expect(state.currentDay).toBe('day2');
    expect(state.plan.day_title).toBe('Day 1');
  });

  it('R06: successful save clears dirty edits made during its request', async () => {
    const result = deferred();
    state.dirty = true;
    state.texts = { title: 'submitted' };
    apiWithMeta.mockReturnValue(result.promise);
    const pending = saveEditorPlan();
    expect(apiWithMeta).toHaveBeenCalled();
    state.texts = { title: 'unsaved later edit' };
    state.dirty = true;
    result.resolve({ data: { ok: true }, version: 'new-version' });
    await pending;
    expect(state.texts.title).toBe('unsaved later edit');
    expect(state.dirty).toBe(false);
  });

  it('R17: direct player URL drops the selected project directory', async () => {
    apiWithMeta.mockResolvedValue({ data: { title: 'A' }, version: 'a' });
    await selectVideo('a.mp4');
    const url = new URL(document.getElementById('player').src);
    expect(url.searchParams.get('project')).toBe('same-name');
    expect(url.searchParams.has('project_dir')).toBe(false);
  });
});
