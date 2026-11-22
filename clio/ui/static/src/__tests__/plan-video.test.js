import { describe, expect, it } from 'vitest';

import {
  findPlanVideo,
  findTimelineVideoIndex,
  videoMatchesPlanIndex,
} from '../plan-video.js';
import { buildTimeline } from '../plan-timeline.js';

const current = {
  file: '197_GX011275.mp4',
  index: '197',
  analysis_indexes: ['016', '197'],
};

describe('plan video identity resolution', () => {
  it('matches an exact current index', () => {
    expect(videoMatchesPlanIndex(current, '197')).toBe(true);
    expect(findPlanVideo([current], '197')).toBe(current);
  });

  it('maps a unique legacy analysis index to the current compressed video', () => {
    expect(videoMatchesPlanIndex(current, '016')).toBe(true);
    expect(findPlanVideo([current], '016')).toBe(current);
  });

  it('rejects an ambiguous legacy alias', () => {
    const duplicate = {
      file: '198_GX011275.mp4',
      index: '198',
      analysis_indexes: ['016'],
    };
    expect(findPlanVideo([current, duplicate], '016')).toBeNull();
  });

  it('returns null when no video claims the plan index', () => {
    expect(findPlanVideo([current], '999')).toBeNull();
  });

  it('maps a current video back to the unique plan timeline index', () => {
    const timeline = buildTimeline([
      { index: '016', use_timeline: '00:10-00:15' },
    ]);
    expect(findTimelineVideoIndex(current, timeline)).toBe('016');
  });
});