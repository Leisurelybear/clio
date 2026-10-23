import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { state } from '../state.js';
import { applyLocale } from '../i18n.js';
import { renderTexts, renderTranscript } from '../editor-texts.js';
import { renderVoiceover } from '../editor-voiceover.js';
import { renderRefineUI } from '../editor-refine.js';
import { updateProjectSidebar, updateSaveBtn } from '../utils.js';
import { api } from '../api.js';

function resetEditorState() {
  state.currentVideo = null;
  state.texts = null;
  state.transcript = null;
  state.voiceover = null;
  state.dirty = false;
  state.currentProject = null;
  state.currentProjectName = null;
  state.currentProjectDir = null;
  state.projectName = '';
  state.config = null;
}

beforeEach(() => {
  resetEditorState();
  document.body.innerHTML = `
    <div id="tab-texts"></div>
    <div id="tab-transcript"></div>
    <div id="tab-voiceover"></div>
    <button id="btn-save"></button>
    <button id="btn-project-switcher"></button>
    <span id="proj-name"></span>
    <div id="project-menu-path"></div>
    <button id="btn-reveal-project"></button>
    <div id="modal-auth"></div>
  `;
});

afterEach(() => {
  applyLocale('zh-CN');
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('editor pane copy follows the active locale', () => {
  it('renders the texts editor in the active locale', () => {
    applyLocale('en');
    state.currentVideo = 'a.mp4';
    state.texts = { title: 'T', timeline: [] };

    renderTexts();

    const html = document.getElementById('tab-texts').innerHTML;
    expect(html).toContain('Basic info');
    expect(html).toContain('Timeline \u2014 0 segments');
    expect(html).toContain('Click a segment to jump to that time');
    expect(html).toContain('Suggested use');
  });

  it('renders the transcript editor in the active locale', () => {
    applyLocale('en');
    state.currentVideo = 'a.mp4';
    state.transcript = { ok: true, segments: [] };

    renderTranscript();

    const html = document.getElementById('tab-transcript').innerHTML;
    expect(html).toContain('Speech transcript (ASR) \u2014 0 segments');
    expect(html).toContain('Add manual transcript');
  });

  it('renders the voiceover editor in the active locale', () => {
    applyLocale('en');
    state.currentVideo = 'a.mp4';
    state.voiceover = { title: 'T' };

    renderVoiceover();

    const html = document.getElementById('tab-voiceover').innerHTML;
    expect(html).toContain('Voiceover script');
    expect(html).toContain('Editing tip');
    expect(html).toContain('Estimated duration (s)');
  });

  it('renders the refine panel in the active locale', () => {
    applyLocale('en');

    const html = renderRefineUI('texts');

    expect(html).toContain('AI review');
    expect(html).toContain('Temporary context');
  });

  it('keeps zh-CN copy for the default locale', () => {
    applyLocale('zh-CN');
    state.currentVideo = 'a.mp4';
    state.texts = { title: 'T', timeline: [] };

    renderTexts();

    const html = document.getElementById('tab-texts').innerHTML;
    expect(html).toContain('\u57fa\u7840\u4fe1\u606f');
    expect(html).toContain('\u5efa\u8bae\u7528\u9014');
  });
});

describe('shell copy follows the active locale', () => {
  it('localizes the save button label', () => {
    applyLocale('en');
    state.dirty = true;
    updateSaveBtn();
    expect(document.getElementById('btn-save').textContent).toBe('Save (unsaved changes)');

    state.dirty = false;
    updateSaveBtn();
    expect(document.getElementById('btn-save').textContent).toBe('Save');
  });

  it('keeps the save icon and re-translates the dirty label on a language switch', () => {
    const save = document.getElementById('btn-save');
    save.innerHTML = '<span class="icon"></span> <span id="btn-save-label" data-i18n="common.save">\u4fdd\u5b58</span>';

    applyLocale('zh-CN');
    state.dirty = true;
    updateSaveBtn();

    const label = document.getElementById('btn-save-label');
    expect(label.textContent).toBe('\u4fdd\u5b58 (\u6709\u6539\u52a8)');
    expect(label.getAttribute('data-i18n')).toBe('common.save_dirty');
    expect(save.querySelector('.icon')).not.toBeNull();

    applyLocale('en');

    expect(document.getElementById('btn-save-label').textContent).toBe('Save (unsaved changes)');
    expect(save.querySelector('.icon')).not.toBeNull();
  });

  it('localizes the project switcher empty state', () => {
    applyLocale('en');

    updateProjectSidebar();

    expect(document.getElementById('proj-name').textContent).toBe('Select project');
    expect(document.getElementById('project-menu-path').textContent).toBe('No project open');
    expect(document.getElementById('btn-project-switcher').title)
      .toBe('Select or manage the current project');
  });

  it('localizes the 401 auth error', async () => {
    applyLocale('en');
    vi.stubGlobal('fetch', vi.fn(async () => ({ status: 401, ok: false })));

    await expect(api('GET', '/api/ping')).rejects.toThrow('HTTP 401: API token required');
  });
});
