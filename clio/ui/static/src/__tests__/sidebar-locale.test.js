import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../runner.js', () => ({ updateRunFilesBadge: vi.fn() }));

import { applyLocale } from '../i18n.js';

describe('sidebar locale re-render', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <button id="btn-select-videos" style="display:none"></button>
      <ul id="video-list"></ul>
      <div id="video-filter-chips"></div>
      <div id="stage-count-bar"></div>
      <div id="offline-summary"></div>
      <span id="video-count"></span>
    `;
  });

  afterEach(() => applyLocale('zh-CN'));

  it('re-renders the select button when the locale changes', async () => {
    const { state } = await import('../state.js');
    state.videos = [{ file: 'a.mp4', index: '001' }];
    state.currentEntity = 'run';
    state.selectionMode = true;
    state.selectedFiles = ['a.mp4'];

    const { updateSelectBtnVisibility } = await import('../sidebar-data.js');
    updateSelectBtnVisibility();
    expect(document.getElementById('btn-select-videos').innerHTML).toContain('\u53d6\u6d88\u9009\u62e9');

    applyLocale('en');
    expect(document.getElementById('btn-select-videos').innerHTML).toContain('Deselect');

    applyLocale('zh-CN');
    expect(document.getElementById('btn-select-videos').innerHTML).toContain('\u53d6\u6d88\u9009\u62e9');
  });

  it('re-renders the video list empty state on a language switch', async () => {
    const { state } = await import('../state.js');
    state.videos = [];
    state.source = 'compressed';
    state.currentEntity = 'video';

    const { renderVideoList } = await import('../sidebar-data.js');
    renderVideoList();
    const zhHtml = document.getElementById('video-list').innerHTML;
    expect(zhHtml.length).toBeGreaterThan(0);

    applyLocale('en');
    const enHtml = document.getElementById('video-list').innerHTML;
    expect(enHtml).not.toBe(zhHtml);
    expect(enHtml).toMatch(/[A-Za-z]/);
  });
});
