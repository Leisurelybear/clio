import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({ api: vi.fn() }));

import { api } from '../api.js';
import {
  configurePlanReadiness,
  getPlanReadiness,
  refreshPlanReadiness,
} from '../editor-plan-readiness.js';
import { state } from '../state.js';

describe('plan readiness panel', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div id="plan-readiness"></div>
      <select id="cut-source"><option value="original" selected>original</option></select>
      <button id="btn-cut-exec"></button>
      <button id="btn-jianying-export"></button>`;
    state.plan = { sequence: [{ index: '001', use_timeline: '00:00-00:03' }] };
    state.currentDay = 'day1';
    configurePlanReadiness({});
    api.mockReset();
  });

  it('renders issues, gates actions, and routes segment clicks', async () => {
    const onIssueClick = vi.fn();
    configurePlanReadiness({ onIssueClick });
    api.mockResolvedValue({
      ok: false,
      errors: [{ segment_index: 1, message: 'segment 2 is too short' }],
      warnings: [{ code: 'duration_drift' }],
    });

    await refreshPlanReadiness();

    expect(document.getElementById('plan-readiness').textContent).toContain('segment 2 is too short');
    expect(document.getElementById('btn-cut-exec').disabled).toBe(true);
    expect(document.getElementById('btn-jianying-export').disabled).toBe(true);
    expect(getPlanReadiness().errors).toHaveLength(1);
    expect(getPlanReadiness().warnings).toHaveLength(1);

    document.querySelector('[data-seg="1"]').click();
    expect(onIssueClick).toHaveBeenCalledWith(1);
  });

  it('keeps actions blocked when the readiness request fails', async () => {
    api.mockRejectedValue(new Error('offline'));

    await refreshPlanReadiness();

    expect(document.getElementById('plan-readiness').textContent).toContain('offline');
    expect(document.getElementById('btn-cut-exec').disabled).toBe(true);
    expect(getPlanReadiness()).toMatchObject({
      ok: false,
      errors: [{ message: 'offline' }],
      warnings: [],
    });
  });
});