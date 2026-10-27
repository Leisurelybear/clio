import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../runner.js', () => ({ updateRunFilesBadge: vi.fn() }));

import { applyLocale } from '../i18n.js';

const VIDEOS = [
  { file: '001_alpha.mp4', index: '001', title: 'Alpha' },
  { file: '002_beta.mp4', index: '002', title: 'Beta' },
  { file: '003_alphabet.mp4', index: '003', title: 'Alphabet' },
];

async function setupSelection({ filter, selected = [] }) {
  document.body.innerHTML = `
    <ul id="video-list"></ul>
    <div id="video-filter-chips"></div>
    <div id="stage-count-bar"></div>
    <div id="offline-summary" hidden></div>
    <span id="video-count"></span>
  `;
  const { state } = await import('../state.js');
  state.videos = VIDEOS.map((video) => ({ ...video }));
  state.source = 'original';
  state.currentEntity = 'run';
  state.selectionMode = true;
  state.selectedFiles = [...selected];
  state.videoFilter = { q: '', stage: '', mode: 'missing', ...filter };
  const { renderVideoList } = await import('../sidebar-data.js');
  renderVideoList();
  return state;
}

describe('sidebar bulk selection under a filter', () => {
  beforeEach(() => applyLocale('zh-CN'));

  it('select-all only selects rows matching the name filter', async () => {
    const state = await setupSelection({ filter: { q: 'alpha' } });

    document.querySelector('[data-action="all"]').click();

    expect(state.selectedFiles).toEqual(['001_alpha.mp4', '003_alphabet.mp4']);
    expect(document.querySelector('.selection-count').textContent).toBe('已选: 2/3');
  });

  it('deselect-all keeps selections outside the filter', async () => {
    const state = await setupSelection({
      filter: { q: 'alpha' },
      selected: ['001_alpha.mp4', '002_beta.mp4', '003_alphabet.mp4'],
    });

    document.querySelector('[data-action="all"]').click();

    expect(state.selectedFiles).toEqual(['002_beta.mp4']);
  });

  it('select-all merges with selections made under another filter', async () => {
    const state = await setupSelection({
      filter: { q: 'beta' },
      selected: ['001_alpha.mp4'],
    });

    document.querySelector('[data-action="all"]').click();

    expect(state.selectedFiles).toEqual(['001_alpha.mp4', '002_beta.mp4']);
    expect(document.querySelector('[data-action="all"]').textContent).toBe('取消全选');
  });

  it('select-all skips offline rows', async () => {
    const state = await setupSelection({ filter: {} });
    state.videos[2].missing = true;
    const { renderVideoList } = await import('../sidebar-data.js');
    renderVideoList();

    document.querySelector('[data-action="all"]').click();

    expect(state.selectedFiles).toEqual(['001_alpha.mp4', '002_beta.mp4']);
  });
});
