import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { openPlanInsertModal } from '../editor-plan-insert.js';
import { state } from '../state.js';

describe('plan insert dialog', () => {
  beforeAll(() => {
    document.body.innerHTML = `
      <div id="modal-plan-insert" class="modal" style="display:none">
        <div class="modal-backdrop"></div>
        <p id="plan-insert-hint"></p>
        <input id="plan-insert-filter">
        <div id="plan-insert-list"></div>
        <button id="plan-insert-cancel"></button>
        <button id="plan-insert-confirm" disabled></button>
      </div>`;
  });

  beforeEach(() => {
    state.plan = { sequence: [{ index: '001', title: 'intro' }] };
    state.videos = [
      { index: '001', title: 'intro', file: '001.mp4' },
      { index: '002', title: 'beach', file: '002.mp4' },
    ];
    document.getElementById('plan-insert-filter').value = '';
    document.getElementById('plan-insert-confirm').disabled = true;
  });

  it('filters videos and reports the inserted sequence to the owner', () => {
    const onInsert = vi.fn();
    openPlanInsertModal(0, onInsert);

    expect(document.getElementById('modal-plan-insert').style.display).toBe('flex');
    expect(document.querySelectorAll('.plan-insert-item')).toHaveLength(2);

    const filter = document.getElementById('plan-insert-filter');
    filter.value = 'beach';
    filter.dispatchEvent(new Event('input'));

    const items = document.querySelectorAll('.plan-insert-item');
    expect(items).toHaveLength(1);
    expect(items[0].textContent).toContain('beach');

    items[0].click();
    const confirmBtn = document.getElementById('plan-insert-confirm');
    expect(confirmBtn.disabled).toBe(false);
    confirmBtn.click();

    expect(onInsert).toHaveBeenCalledWith([
      { index: '001', title: 'intro' },
      {
        index: '002',
        title: 'beach',
        reason: '',
        use_timeline: '',
        voiceover_hint: '',
      },
    ], 1);
    expect(document.getElementById('modal-plan-insert').style.display).toBe('none');
  });
});