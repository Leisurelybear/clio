import { beforeEach, describe, expect, it, vi } from 'vitest';
import { seedCatalogs } from './catalogs.js';
import { state } from '../state.js';
import {
  renderFilterChips,
  renderOfflineSummary,
  renderStageCountBar,
} from '../sidebar-video-overview.js';

beforeEach(() => {
  seedCatalogs();
  document.body.innerHTML = `
    <div id="offline-summary"></div>
    <div id="video-filter-chips"></div>
    <div id="stage-count-bar"></div>
  `;
  state.videos = [];
  state.source = 'original';
  state.videoFilter = { q: '', stage: '', mode: 'missing' };
});

describe('sidebar video overview', () => {
  it('shows the offline batch relink summary only in original view', () => {
    state.videos = [{ file: '001_a.mp4', missing: true }];

    renderOfflineSummary();

    const box = document.getElementById('offline-summary');
    expect(box.hidden).toBe(false);
    expect(box.textContent).toContain('1');
    expect(box.querySelector('#btn-batch-relink')).not.toBeNull();

    state.source = 'compressed';
    renderOfflineSummary();
    expect(box.hidden).toBe(true);
    expect(box.innerHTML).toBe('');
  });

  it('updates the missing-stage filter and notifies the list renderer', () => {
    const onChange = vi.fn();
    state.videos = [{ file: '001_a.mp4' }];

    renderFilterChips(onChange);
    const chips = document.querySelectorAll('.video-filter-chip');
    expect(chips.length).toBeGreaterThan(1);

    chips[1].click();

    expect(state.videoFilter.stage).toBe('compress');
    expect(state.videoFilter.mode).toBe('missing');
    expect(onChange).toHaveBeenCalledOnce();
  });

  it('switches a completed stage to the done filter', () => {
    const onChange = vi.fn();
    state.videos = [{ file: '001_a.mp4', text_json: '001_a.json' }];

    renderStageCountBar(onChange);
    const cells = document.querySelectorAll('.stage-count-cell');
    cells[1].click();

    expect(state.videoFilter.stage).toBe('analyze');
    expect(state.videoFilter.mode).toBe('done');
    expect(onChange).toHaveBeenCalledOnce();
  });
});
