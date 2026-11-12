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

import { api, apiWithMeta } from '../api.js';
import { applyLocale } from '../i18n.js';
import { _prefillSubtitle, save } from '../editor-plan.js';
import { saveEditorPlan } from '../editor-plan-persistence.js';
import { state, artifactVersion } from '../state.js';
import { addToast } from '../toast.js';
import { markDirty, setStatus } from '../utils.js';

function resetState() {
  Object.assign(state, {
    currentEntity: 'video',
    currentVideo: 'a.mp4',
    currentDay: 'day1',
    currentTab: 'texts',
    dirty: true,
    configTab: 'project',
    configGlobal: null,
    configProject: null,
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
  it('keeps inferred narration as a placeholder instead of editing the plan', async () => {
    state.videos = [{ index: '001', script_json: 'a_script.json' }];
    const seg = { index: '001' };
    const subEl = { value: '', placeholder: '' };
    const li = { querySelector: vi.fn(() => subEl) };
    apiWithMeta.mockResolvedValue({ data: { voiceover: 'AI 建议旁白' }, version: 'v1' });

    _prefillSubtitle(li, seg);
    await vi.waitFor(() => expect(subEl.placeholder).toBe('AI 建议旁白'));

    expect(subEl.value).toBe('');
    expect(seg).toEqual({ index: '001' });
    expect(markDirty).not.toHaveBeenCalled();
  });
});

describe('save routing', () => {
  it('saves the active global config and refreshes dependent UI', async () => {
    state.currentEntity = 'config';
    state.configTab = 'global';
    state.configGlobal = { ui: { language: 'en' }, ai: {} };
    state.dirty = true;
    api.mockResolvedValue({ ok: true });
    window.refreshFfmpegDepsUi = vi.fn().mockResolvedValue(undefined);

    await save();

    expect(api).toHaveBeenCalledWith('PUT', '/api/config/global', state.configGlobal);
    expect(applyLocale).toHaveBeenCalledWith('en');
    expect(window.refreshFfmpegDepsUi).toHaveBeenCalledOnce();
    expect(state.dirty).toBe(false);
    expect(setStatus).toHaveBeenCalledWith('plan.config_saved_global', 'ok');
    expect(addToast).toHaveBeenCalledWith(
      'plan.config_saved_global',
      'success',
      undefined,
      { persist: false },
    );
    delete window.refreshFfmpegDepsUi;
  });

  it('saves the active project config without applying the global language', async () => {
    state.currentEntity = 'config';
    state.configTab = 'project';
    state.configProject = { plan: { max_clips: 10 } };
    state.dirty = true;
    api.mockResolvedValue({ ok: true });
    window.refreshFfmpegDepsUi = vi.fn().mockResolvedValue(undefined);

    await save();

    expect(api).toHaveBeenCalledWith('PUT', '/api/config/project', state.configProject);
    expect(applyLocale).not.toHaveBeenCalled();
    expect(state.dirty).toBe(false);
    expect(setStatus).toHaveBeenCalledWith('plan.config_saved_project', 'ok');
    delete window.refreshFfmpegDepsUi;
  });

  it('keeps dirty config data when the server returns a save error', async () => {
    state.currentEntity = 'config';
    state.configTab = 'project';
    state.configProject = { plan: { max_clips: 10 } };
    state.dirty = true;
    api.mockResolvedValue({ error: 'read-only file' });

    await save();

    expect(state.dirty).toBe(true);
    expect(setStatus).toHaveBeenCalledWith('plan.save_failed', 'err');
    expect(addToast).toHaveBeenCalledWith(
      'plan.save_failed',
      'error',
      6000,
      { title: 'plan.save_failed_title' },
    );
  });

  it('notifies the plan readiness hook after a successful plan save', async () => {
    state.currentEntity = 'plan';
    state.plan = { day_title: 'draft', sequence: [] };
    state.artifactVersions.plan.day1 = 'old-plan';
    apiWithMeta.mockResolvedValue({ data: { ok: true }, version: 'new-plan' });
    const onPlanSaved = vi.fn();

    await saveEditorPlan({ onPlanSaved });

    expect(onPlanSaved).toHaveBeenCalledOnce();
    expect(state.dirty).toBe(false);
    expect(artifactVersion('plan', 'day1')).toBe('new-plan');
  });

  it('does not call the save API when the editor is clean', async () => {
    state.dirty = false;

    await save();

    expect(api).not.toHaveBeenCalled();
    expect(apiWithMeta).not.toHaveBeenCalled();
    expect(setStatus).toHaveBeenCalledWith('plan.nothing_to_save', 'warn');
  });

  it('reports a no-op target instead of claiming a successful save', async () => {
    state.currentEntity = 'run';
    state.dirty = true;

    await save();

    expect(api).not.toHaveBeenCalled();
    expect(apiWithMeta).not.toHaveBeenCalled();
    expect(setStatus).toHaveBeenCalledWith('editor.save.no_target', 'warn');
  });

  it('surfaces unexpected artifact save failures without clearing dirty state', async () => {
    state.texts = { title: 'draft' };
    apiWithMeta.mockRejectedValue(new Error('network down'));

    await save();

    expect(state.dirty).toBe(true);
    expect(setStatus).toHaveBeenCalledWith('plan.save_failed', 'err');
  });
});
