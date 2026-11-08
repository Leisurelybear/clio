import { beforeEach, describe, expect, it, vi } from 'vitest';

import { deletePlan } from '../editor-plan.js';
import { state } from '../state.js';

function seedState(overrides = {}) {
  Object.assign(state, {
    currentDay: 'day1',
    plan: { sequence: [] },
    planPreviewMedia: { segments: [] },
    previewCutFallbacks: new Set([0]),
    _previewMedia: null,
    availablePlans: [{ day_label: 'day1' }, { day_label: 'day2' }],
    ...overrides,
  });
}

function fakeSidebar(loadPlansImpl) {
  return {
    loadPlans: vi.fn(loadPlansImpl || (async () => {})),
    selectPlan: vi.fn(async () => {}),
    saveProject: vi.fn(),
  };
}

describe('deletePlan', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="tab-plan"></div><div id="player-pane"></div>';
  });

  it('deletes the file, clears plan state, and selects the next plan', async () => {
    seedState();
    const request = vi.fn(async () => ({ ok: true }));
    const sidebar = fakeSidebar(async () => {
      state.availablePlans = [{ day_label: 'day2' }];
    });

    expect(await deletePlan('day1', {
      api: request,
      confirm: () => true,
      sidebar,
    })).toBe(true);

    expect(request).toHaveBeenCalledWith('DELETE', '/api/plan?day=day1');
    expect(state.plan).toBeNull();
    expect(state.planPreviewMedia).toBeNull();
    expect(state.previewCutFallbacks.size).toBe(0);
    expect(sidebar.selectPlan).toHaveBeenCalledWith('day2');
  });

  it('does nothing when the confirmation is cancelled', async () => {
    seedState();
    const request = vi.fn(async () => ({ ok: true }));
    const sidebar = fakeSidebar();

    expect(await deletePlan('day1', {
      api: request,
      confirm: () => false,
      sidebar,
    })).toBe(false);

    expect(request).not.toHaveBeenCalled();
    expect(state.plan).toEqual({ sequence: [] });
    expect(sidebar.loadPlans).not.toHaveBeenCalled();
  });

  it('keeps the current plan when the delete request fails', async () => {
    seedState();
    const request = vi.fn(async () => { throw new Error('HTTP 500: nope'); });
    const sidebar = fakeSidebar();

    expect(await deletePlan('day1', {
      api: request,
      confirm: () => true,
      sidebar,
    })).toBe(false);

    expect(state.plan).toEqual({ sequence: [] });
    expect(sidebar.loadPlans).not.toHaveBeenCalled();
  });

  it('renders the empty plan pane when no plans remain', async () => {
    seedState({ availablePlans: [{ day_label: 'day1' }] });
    const request = vi.fn(async () => ({ ok: true }));
    const sidebar = fakeSidebar(async () => {
      state.availablePlans = [];
    });

    expect(await deletePlan('day1', {
      api: request,
      confirm: () => true,
      sidebar,
    })).toBe(true);

    const { t } = await import('../i18n.js');
    expect(state.currentDay).toBe('day1');
    expect(sidebar.saveProject).toHaveBeenCalled();
    expect(document.getElementById('tab-plan').innerHTML).toContain(t('plan.empty_title'));
  });
});
