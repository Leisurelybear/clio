import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api.js';
import { save } from '../editor-plan.js';
import { refineCurrentFile } from '../editor-refine.js';
import { state } from '../state.js';

vi.mock('../api.js', () => ({ api: vi.fn() }));
vi.mock('../editor-plan.js', () => ({ save: vi.fn() }));
vi.mock('../confirm.js', () => ({
  confirmDialog: vi.fn(async () => true),
  alertDialog: vi.fn(async () => undefined),
  promptDialog: vi.fn(async () => ''),
}));
vi.mock('../editor.js', () => ({
  renderActiveTab: vi.fn(),
  renderTexts: vi.fn(),
  renderVoiceover: vi.fn(),
}));

function resetState() {
  Object.assign(state, {
    currentEntity: 'video',
    currentVideo: 'a.mp4',
    currentTab: 'texts',
    dirty: false,
    texts: { title: 'old', timeline: [] },
    voiceover: null,
    videos: [{ file: 'a.mp4', text_json: 'a.json', script_json: 'a_script.json' }],
    refining: null,
    _refineError: null,
  });
}

beforeEach(() => {
  resetState();
  document.body.innerHTML = `
    <textarea id="refine-context-texts"></textarea>
    <textarea id="refine-context-scripts"></textarea>
    <p id="status"></p>
  `;
  vi.clearAllMocks();
});

describe('refineCurrentFile dirty-state handling', () => {
  it('saves unsaved edits before requesting AI refinement', async () => {
    state.dirty = true;
    const order = [];
    save.mockImplementation(async () => {
      order.push('save');
      state.dirty = false;
    });
    api.mockImplementation(async () => {
      order.push('refine');
      return { ok: true, data: { title: 'fixed', timeline: [] } };
    });

    await refineCurrentFile('texts');

    expect(order).toEqual(['save', 'refine']);
    expect(state.texts).toEqual({ title: 'fixed', timeline: [] });
  });

  it('does not request AI refinement when saving the dirty artifact fails', async () => {
    state.dirty = true;
    save.mockImplementation(async () => {});

    await refineCurrentFile('texts');

    expect(save).toHaveBeenCalledTimes(1);
    expect(api).not.toHaveBeenCalled();
  });

  it('skips saving when the artifact is already clean', async () => {
    api.mockResolvedValue({ ok: true, data: { title: 'fixed', timeline: [] } });

    await refineCurrentFile('texts');

    expect(save).not.toHaveBeenCalled();
    expect(api).toHaveBeenCalledWith('POST', '/api/refine', {
      file: 'a.json',
      type: 'texts',
      context: undefined,
    });
  });
});
