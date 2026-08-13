import { describe, it, expect } from 'vitest';
import {
  buildTimeline,
  clampGlobal,
  globalToLocal,
  localToGlobal,
  nextPlayableSegIndex,
  segmentWidths,
  locateSegmentByPlanSec,
} from '../plan-timeline.js';

const seq3 = [
  { index: '001', use_timeline: '00:10-00:20' }, // 10s
  { index: '002', use_timeline: '01:00-01:05' }, // 5s
  { index: '003', use_timeline: '00:00-00:15' }, // 15s
];

describe('buildTimeline', () => {
  it('empty sequence', () => {
    const t = buildTimeline([]);
    expect(t.segments).toEqual([]);
    expect(t.total).toBe(0);
  });

  it('single segment', () => {
    const t = buildTimeline([{ index: '001', use_timeline: '00:00-00:30' }]);
    expect(t.total).toBe(30);
    expect(t.segments[0]).toMatchObject({
      segIndex: 0,
      videoIndex: '001',
      planStart: 0,
      planEnd: 30,
      duration: 30,
      globalStart: 0,
      globalEnd: 30,
    });
  });

  it('multi cumulative', () => {
    const t = buildTimeline(seq3);
    expect(t.total).toBe(30);
    expect(t.segments.map((s) => s.globalStart)).toEqual([0, 10, 15]);
    expect(t.segments.map((s) => s.globalEnd)).toEqual([10, 15, 30]);
  });

  it('invalid timeline → duration 0', () => {
    const t = buildTimeline([{ index: 'x', use_timeline: '' }]);
    expect(t.segments[0].duration).toBe(0);
    expect(t.total).toBe(0);
  });

  it('end < start → duration 0', () => {
    const t = buildTimeline([{ index: 'x', use_timeline: '00:40-00:10' }]);
    expect(t.segments[0].duration).toBe(0);
  });
});

describe('clampGlobal / maps', () => {
  it('clamp', () => {
    const t = buildTimeline(seq3);
    expect(clampGlobal(t, -5)).toBe(0);
    expect(clampGlobal(t, 100)).toBe(30);
    expect(clampGlobal(t, 12)).toBe(12);
  });

  it('globalToLocal mid second seg', () => {
    const t = buildTimeline(seq3);
    const loc = globalToLocal(t, 12);
    expect(loc.segIndex).toBe(1);
    expect(loc.localSec).toBe(2);
    expect(loc.planSec).toBe(62); // 01:00 + 2
    expect(loc.videoIndex).toBe('002');
  });

  it('t at boundary globalEnd maps to next start', () => {
    const t = buildTimeline(seq3);
    const loc = globalToLocal(t, 10);
    expect(loc.segIndex).toBe(1);
    expect(loc.localSec).toBe(0);
    expect(loc.planSec).toBe(60);
  });

  it('localToGlobal inverse', () => {
    const t = buildTimeline(seq3);
    expect(localToGlobal(t, 1, 2)).toBe(12);
    expect(localToGlobal(t, 0, 0)).toBe(0);
  });

  it('globalToLocal null on empty', () => {
    expect(globalToLocal(buildTimeline([]), 0)).toBeNull();
  });
});

describe('nextPlayableSegIndex / widths', () => {
  it('skips zero duration', () => {
    const t = buildTimeline([
      { index: 'a', use_timeline: '00:00-00:10' },
      { index: 'b', use_timeline: '' },
      { index: 'c', use_timeline: '00:00-00:05' },
    ]);
    expect(nextPlayableSegIndex(t, -1)).toBe(0);
    expect(nextPlayableSegIndex(t, 0)).toBe(2);
    expect(nextPlayableSegIndex(t, 2)).toBeNull();
  });

  it('globalToLocal at end with trailing zero-duration uses last playable', () => {
    const t = buildTimeline([
      { index: 'a', use_timeline: '00:00-00:10' },
      { index: 'b', use_timeline: '' },
      { index: 'c', use_timeline: '' },
    ]);
    const loc = globalToLocal(t, 10);
    expect(loc.segIndex).toBe(0);
    expect(loc.localSec).toBe(10);
  });

  it('widths proportional', () => {
    const t = buildTimeline(seq3);
    const w = segmentWidths(t);
    expect(w[0]).toBeCloseTo(10 / 30);
    expect(w[1]).toBeCloseTo(5 / 30);
    expect(w[2]).toBeCloseTo(15 / 30);
  });

  it('all zero → equal widths', () => {
    const t = buildTimeline([
      { index: 'a', use_timeline: '' },
      { index: 'b', use_timeline: '00:10-00:05' },
    ]);
    expect(segmentWidths(t)).toEqual([0.5, 0.5]);
  });
});

describe('locateSegmentByPlanSec', () => {
  const multiSameVideo = [
    { index: '001', use_timeline: '00:10-00:20' }, // plan 10-20
    { index: '002', use_timeline: '01:00-01:05' },
    { index: '001', use_timeline: '00:30-00:40' }, // plan 30-40, same video again
  ];

  it('returns null for empty / no video match', () => {
    expect(locateSegmentByPlanSec(buildTimeline([]), '001', 0)).toBeNull();
    const t = buildTimeline(seq3);
    expect(locateSegmentByPlanSec(t, '999', 0)).toBeNull();
  });

  it('hits interior of segment', () => {
    const t = buildTimeline(seq3);
    const loc = locateSegmentByPlanSec(t, '002', 62); // 01:00 + 2
    expect(loc).toMatchObject({
      segIndex: 1,
      localSec: 2,
      planSec: 62,
      videoIndex: '002',
      pastEnd: false,
    });
  });

  it('clamps before first window of that video', () => {
    const t = buildTimeline(seq3);
    const loc = locateSegmentByPlanSec(t, '001', 5);
    expect(loc.segIndex).toBe(0);
    expect(loc.localSec).toBe(0);
    expect(loc.pastEnd).toBe(false);
  });

  it('marks pastEnd when at/after segment end', () => {
    const t = buildTimeline(seq3);
    const loc = locateSegmentByPlanSec(t, '001', 20);
    expect(loc.segIndex).toBe(0);
    expect(loc.pastEnd).toBe(true);
    expect(loc.localSec).toBe(10);
  });

  it('picks later window when same video appears twice', () => {
    const t = buildTimeline(multiSameVideo);
    const loc = locateSegmentByPlanSec(t, '001', 35);
    expect(loc.segIndex).toBe(2);
    expect(loc.localSec).toBe(5);
    expect(loc.pastEnd).toBe(false);
  });
});
