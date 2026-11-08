import { describe, it, expect } from 'vitest';
import {
  reorderSequence,
  removeSegment,
  patchSegment,
  formatTimelineSec,
  setTimelineBound,
  insertSegment,
  computeDropToIndex,
  computeDragAutoScrollDelta,
  nextExpandedAfterDelete,
  nextExpandedAfterInsert,
  nextExpandedAfterMove,
  planSecFromPlayer,
  parseUseTimeline,
  fileSecFromPlan,
  clampFileSelection,
  selectionFromUseTimeline,
  useTimelineFromFileSelection,
  syncPlanTotalEstimatedSec,
} from '../plan-edit.js';

describe('plan-edit', () => {
  const seq = [{ index: '001' }, { index: '002' }, { index: '003' }];

  it('reorders without mutating input', () => {
    const next = reorderSequence(seq, 0, 2);
    expect(next.map((s) => s.index)).toEqual(['002', '003', '001']);
    expect(seq[0].index).toBe('001');
  });

  it('removeSegment', () => {
    const next = removeSegment(seq, 1);
    expect(next.map((s) => s.index)).toEqual(['001', '003']);
    expect(seq).toHaveLength(3);
  });

  it('patchSegment merges fields', () => {
    const s = { index: '001', title: 'a' };
    const next = patchSegment(s, { title: 'b', use_timeline: '00:00-00:01' });
    expect(next.title).toBe('b');
    expect(next.use_timeline).toBe('00:00-00:01');
    expect(s.title).toBe('a');
  });

  it('syncPlanTotalEstimatedSec sums valid ranges into the plan', () => {
    const plan = {
      total_estimated_sec: 999,
      sequence: [
        { index: '001', use_timeline: '00:00-00:10' },
        { index: '002', use_timeline: '00:10-00:15.5' },
      ],
    };

    expect(syncPlanTotalEstimatedSec(plan)).toBe(15.5);
    expect(plan.total_estimated_sec).toBe(15.5);
  });

  it('syncPlanTotalEstimatedSec ignores empty, invalid, and reversed ranges', () => {
    const plan = {
      total_estimated_sec: 999,
      sequence: [
        { index: '001', use_timeline: '00:00-00:04' },
        { index: '002', use_timeline: '' },
        { index: '003', use_timeline: 'not-a-range' },
        { index: '004', use_timeline: '00:20-00:10' },
      ],
    };

    expect(syncPlanTotalEstimatedSec(plan)).toBe(4);
    expect(plan.total_estimated_sec).toBe(4);
  });

  it('syncPlanTotalEstimatedSec sets an empty sequence total to zero', () => {
    const plan = { total_estimated_sec: 180, sequence: [] };

    expect(syncPlanTotalEstimatedSec(plan)).toBe(0);
    expect(plan.total_estimated_sec).toBe(0);
  });

  it('formatTimelineSec pads mm:ss', () => {
    expect(formatTimelineSec(0)).toBe('00:00');
    expect(formatTimelineSec(65)).toBe('01:05');
    expect(formatTimelineSec(3723)).toBe('62:03');
  });

  it('setTimelineBound start keeps or invents end', () => {
    expect(setTimelineBound('00:10-00:40', 'start', 15)).toBe('00:15-00:40');
    expect(setTimelineBound('', 'start', 12)).toBe('00:12-00:17');
    expect(setTimelineBound('00:30-00:20', 'start', 25)).toBe('00:25-00:30');
  });

  it('setTimelineBound end keeps or invents start', () => {
    expect(setTimelineBound('00:10-00:40', 'end', 50)).toBe('00:10-00:50');
    expect(setTimelineBound('', 'end', 20)).toBe('00:15-00:20');
    expect(setTimelineBound('00:30-00:20', 'end', 10)).toBe('00:05-00:10');
  });

  it('planSecFromPlayer subtracts offset used by preview seek', () => {
    expect(planSecFromPlayer(55, 40)).toBe(15);
    expect(planSecFromPlayer(12, 0)).toBe(12);
    expect(planSecFromPlayer(5, 40)).toBe(0); // clamp
    expect(planSecFromPlayer(NaN, 0)).toBeNull();
  });

  it('setTimelineBound with planSecFromPlayer matches preview round-trip', () => {
    // Preview: seek = plan + offset. User at player 55 with offset 40 → plan 15.
    const planSec = planSecFromPlayer(55, 40);
    expect(setTimelineBound('00:00-00:20', 'start', planSec)).toBe('00:15-00:20');
  });

  it('insertSegment inserts after atIndex without mutating', () => {
    const next = insertSegment(seq, 0, { index: '099', title: 'new' });
    expect(next.map((s) => s.index)).toEqual(['001', '099', '002', '003']);
    expect(seq).toHaveLength(3);
    expect(next[1].title).toBe('new');
    expect(next[1].use_timeline).toBe('');
  });

  it('insertSegment at end when atIndex is last', () => {
    const next = insertSegment(seq, 2, { index: '004' });
    expect(next.map((s) => s.index)).toEqual(['001', '002', '003', '004']);
  });

  it('insertSegment at -1 prepends', () => {
    const next = insertSegment(seq, -1, { index: '000' });
    expect(next.map((s) => s.index)).toEqual(['000', '001', '002', '003']);
  });
});

describe('computeDropToIndex', () => {
  const n = 4;

  it('returns null when dropping on own slot (before or after self)', () => {
    expect(computeDropToIndex(1, 1, false, n)).toBeNull();
    expect(computeDropToIndex(1, 1, true, n)).toBeNull();
    expect(computeDropToIndex(0, 0, false, n)).toBeNull();
    expect(computeDropToIndex(0, 0, true, n)).toBeNull();
  });

  it('maps before overIndex when dragging down', () => {
    // [A,B,C,D] drag A(0) before C(2) → final index 1 → [B,A,C,D]
    expect(computeDropToIndex(0, 2, false, n)).toBe(1);
    const seq = ['A', 'B', 'C', 'D'];
    expect(reorderSequence(seq, 0, 1)).toEqual(['B', 'A', 'C', 'D']);
  });

  it('maps after overIndex when dragging down to end-ish', () => {
    // drag A(0) after C(2) → final 2 → [B,C,A,D]
    expect(computeDropToIndex(0, 2, true, n)).toBe(2);
    expect(reorderSequence(['A', 'B', 'C', 'D'], 0, 2)).toEqual(['B', 'C', 'A', 'D']);
  });

  it('maps after last to end', () => {
    // drag A(0) after D(3) → final 3 → [B,C,D,A]
    expect(computeDropToIndex(0, 3, true, n)).toBe(3);
    expect(reorderSequence(['A', 'B', 'C', 'D'], 0, 3)).toEqual(['B', 'C', 'D', 'A']);
  });

  it('maps before first when dragging up', () => {
    // drag C(2) before A(0) → final 0
    expect(computeDropToIndex(2, 0, false, n)).toBe(0);
    expect(reorderSequence(['A', 'B', 'C', 'D'], 2, 0)).toEqual(['C', 'A', 'B', 'D']);
  });

  it('returns null for out-of-range inputs', () => {
    expect(computeDropToIndex(-1, 0, false, n)).toBeNull();
    expect(computeDropToIndex(0, 9, false, n)).toBeNull();
    expect(computeDropToIndex(0, 0, false, 0)).toBeNull();
  });
});

describe('computeDragAutoScrollDelta', () => {
  const top = 100;
  const bottom = 500; // viewport height 400

  it('returns 0 in the middle band', () => {
    expect(computeDragAutoScrollDelta(300, top, bottom)).toBe(0);
  });

  it('scrolls up near top edge (negative delta)', () => {
    const d = computeDragAutoScrollDelta(110, top, bottom, 48, 18);
    expect(d).toBeLessThan(0);
  });

  it('scrolls down near bottom edge (positive delta)', () => {
    const d = computeDragAutoScrollDelta(490, top, bottom, 48, 18);
    expect(d).toBeGreaterThan(0);
  });

  it('faster closer to the extreme edge', () => {
    const near = Math.abs(computeDragAutoScrollDelta(140, top, bottom, 48, 18));
    const far = Math.abs(computeDragAutoScrollDelta(105, top, bottom, 48, 18));
    expect(far).toBeGreaterThanOrEqual(near);
  });
});

describe('nextExpandedAfterDelete', () => {
  it('returns null when list becomes empty', () => {
    expect(nextExpandedAfterDelete(0, 0, 0)).toBeNull();
  });

  it('keeps same index when deleting after expanded', () => {
    expect(nextExpandedAfterDelete(1, 2, 3)).toBe(1);
  });

  it('decrements when deleting before expanded', () => {
    expect(nextExpandedAfterDelete(2, 0, 3)).toBe(1);
  });

  it('clamps to last when deleting the expanded last item', () => {
    expect(nextExpandedAfterDelete(2, 2, 2)).toBe(1);
  });

  it('returns null when expanded was null', () => {
    expect(nextExpandedAfterDelete(null, 1, 2)).toBeNull();
  });
});

describe('nextExpandedAfterInsert', () => {
  it('expands the newly inserted index (afterIndex + 1)', () => {
    expect(nextExpandedAfterInsert(0)).toBe(1);
    expect(nextExpandedAfterInsert(-1)).toBe(0);
    expect(nextExpandedAfterInsert(2)).toBe(3);
  });
});

describe('nextExpandedAfterMove', () => {
  it('follows the moved item when it was expanded', () => {
    expect(nextExpandedAfterMove(0, 2, 0)).toBe(2);
    expect(nextExpandedAfterMove(2, 0, 2)).toBe(0);
  });

  it('shifts expanded when another item moves across it', () => {
    expect(nextExpandedAfterMove(0, 2, 2)).toBe(1);
  });

  it('returns null when nothing expanded', () => {
    expect(nextExpandedAfterMove(0, 2, null)).toBeNull();
  });
});

describe('range picker helpers', () => {
  it('parseUseTimeline reads mm:ss-mm:ss', () => {
    expect(parseUseTimeline('00:10-00:40')).toEqual({ startSec: 10, endSec: 40 });
    expect(parseUseTimeline('')).toBeNull();
    expect(parseUseTimeline('nope')).toBeNull();
    expect(parseUseTimeline('00:10')).toBeNull();
  });

  it('fileSecFromPlan adds positive offset only', () => {
    expect(fileSecFromPlan(15, 40)).toBe(55);
    expect(fileSecFromPlan(15, 0)).toBe(15);
    expect(fileSecFromPlan(15, -5)).toBe(15);
  });

  it('clampFileSelection enforces min span and duration', () => {
    expect(clampFileSelection({ startSec: 10, endSec: 40, duration: 100 }))
      .toEqual({ startSec: 10, endSec: 40 });
    expect(clampFileSelection({ startSec: -5, endSec: 200, duration: 50 }))
      .toEqual({ startSec: 0, endSec: 50 });
    expect(clampFileSelection({ startSec: 20, endSec: 20.5, duration: 100, minSpan: 1 }))
      .toEqual({ startSec: 20, endSec: 21 });
    expect(clampFileSelection({ startSec: 0, endSec: 0.2, duration: 0.5, minSpan: 1 }))
      .toEqual({ startSec: 0, endSec: 0.5 });
  });

  it('selectionFromUseTimeline maps plan→file and defaults empty', () => {
    expect(selectionFromUseTimeline('00:10-00:40', 100, 0))
      .toEqual({ startSec: 10, endSec: 40 });
    expect(selectionFromUseTimeline('00:10-00:40', 200, 40))
      .toEqual({ startSec: 50, endSec: 80 });
    expect(selectionFromUseTimeline('', 100, 0))
      .toEqual({ startSec: 0, endSec: 5 });
    expect(selectionFromUseTimeline('bad', 3, 0))
      .toEqual({ startSec: 0, endSec: 3 });
  });

  it('useTimelineFromFileSelection maps file→plan', () => {
    expect(useTimelineFromFileSelection(55, 80, 40)).toBe('00:15-00:40');
    expect(useTimelineFromFileSelection(10, 25, 0)).toBe('00:10-00:25');
  });
});
