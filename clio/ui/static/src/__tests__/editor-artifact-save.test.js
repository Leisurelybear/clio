import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({
  api: vi.fn(),
  apiWithMeta: vi.fn(),
  icon: vi.fn(() => ''),
}));
vi.mock('../utils.js', async () => {
  const { state } = await import('../state.js');
  return {
    $: vi.fn(),
    escapeHtml: vi.fn((value) => String(value)),
    markDirty: vi.fn(() => { state.dirty = true; }),
    clearDirty: vi.fn(() => { state.dirty = false; }),
    updateSidebarDay: vi.fn(),
    setStatus: vi.fn(),
  };
});
vi.mock('../viewer.js', () => ({
  renderPreviewBar: vi.fn(),
  startPreview: vi.fn(),
  stopPreview: vi.fn(),
  _playPreviewSegment: vi.fn(),
  seekToGlobal: vi.fn(),
}));
vi.mock('../plan-timeline.js', () => ({ buildTimeline: vi.fn(), clampGlobal: vi.fn() }));
vi.mock('../waveform.js', () => ({
  recomposePlanWaveformFromCache: vi.fn(),
  isPlanWaveformMode: vi.fn(() => false),
}));
vi.mock('../toast.js', () => ({ addToast: vi.fn() }));
vi.mock('../i18n.js', () => ({
  applyLocale: vi.fn(),
  getLocale: vi.fn(() => 'zh-CN'),
  onLocaleChange: vi.fn(),
  t: vi.fn((key) => key),
}));
vi.mock('../plan-export.js', () => ({ exportJianyingDraft: vi.fn() }));
vi.mock('../task-center.js', () => ({ waitForTask: vi.fn() }));
vi.mock('../plan-range-picker.js', () => ({ openPlanRangePicker: vi.fn() }));
vi.mock('../plan-edit.js', () => ({
  reorderSequence: vi.fn(),
  removeSegment: vi.fn(),
  patchSegment: vi.fn(),
  insertSegment: vi.fn(),
  computeDropToIndex: vi.fn(),
  computeDragAutoScrollDelta: vi.fn(),
  nextExpandedAfterDelete: vi.fn(),
  nextExpandedAfterInsert: vi.fn(),
  nextExpandedAfterMove: vi.fn(),
  syncPlanTotalEstimatedSec: vi.fn(),
  createPlanHistory: vi.fn(() => ({ undo: [], redo: [], limit: 50 })),
  resetPlanHistory: vi.fn(),
  planSnapshot: vi.fn(() => 'null'),
  pushPlanHistory: vi.fn(),
  undoPlanSnapshot: vi.fn(),
  redoPlanSnapshot: vi.fn(),
}));
vi.mock('../latest.js', () => ({
  beginLatest: vi.fn(),
  endLatest: vi.fn(),
  isLatest: vi.fn(() => true),
  isAbortError: vi.fn(() => false),
}));

import { apiWithMeta } from '../api.js';
import { save } from '../editor-plan.js';
import { state, artifactVersion } from '../state.js';
import { addToast } from '../toast.js';
import { setStatus } from '../utils.js';

function resetState() {
  Object.assign(state, {
    currentEntity: 'video',
    currentVideo: 'a.mp4',
    currentDay: 'day1',
    currentTab: 'texts',
    dirty: true,
    plan: null,
    texts: null,
    voiceover: null,
    videos: [{
      file: 'a.mp4',
      text_json: 'a.json',
      script_json: 'a_script.json',
    }],
    artifactVersions: { plan: {}, texts: {}, voiceover: {} },
  });
}

function conflictError() {
  return Object.assign(new Error('HTTP 409: changed elsewhere'), {
    status: 409,
    body: { ok: false, code: 'artifact_conflict', current_version: 'current' },
  });
}

beforeEach(() => {
  resetState();
  vi.clearAllMocks();
});

describe('artifact saves', () => {
  it('sends If-Match and stores the returned version after a successful save', async () => {
    state.texts = { title: 'edited' };
    state.artifactVersions.texts['a.json'] = 'old-version';
    apiWithMeta.mockResolvedValue({ data: { ok: true }, version: 'new-version' });

    await save();

    expect(apiWithMeta).toHaveBeenCalledWith(
      'PUT',
      '/api/texts?file=a.json',
      { title: 'edited' },
      { headers: { 'If-Match': '"old-version"' } },
    );
    expect(state.dirty).toBe(false);
    expect(artifactVersion('texts', 'a.json')).toBe('new-version');
  });

  it.each([
    {
      label: 'plan',
      setup: () => {
        state.currentEntity = 'plan';
        state.plan = { day_title: 'draft', sequence: [] };
        state.artifactVersions.plan.day1 = 'old-plan';
      },
      pointer: () => state.plan,
    },
    {
      label: 'texts',
      setup: () => {
        state.currentTab = 'texts';
        state.texts = { title: 'draft text' };
        state.artifactVersions.texts['a.json'] = 'old-texts';
      },
      pointer: () => state.texts,
    },
    {
      label: 'voiceover',
      setup: () => {
        state.currentTab = 'voiceover';
        state.voiceover = { voiceover: 'draft voiceover' };
        state.artifactVersions.voiceover['a_script.json'] = 'old-voiceover';
      },
      pointer: () => state.voiceover,
    },
  ])('keeps dirty $label data when the server reports a conflict', async ({ setup, pointer }) => {
    setup();
    const original = pointer();
    apiWithMeta.mockRejectedValue(conflictError());

    await save();

    expect(state.dirty).toBe(true);
    expect(pointer()).toBe(original);
    expect(setStatus).toHaveBeenCalledWith('plan.save_conflict', 'err');
    expect(addToast).toHaveBeenCalledWith(
      'plan.save_conflict',
      'error',
      8000,
      { title: 'plan.save_conflict_title' },
    );
  });
});
