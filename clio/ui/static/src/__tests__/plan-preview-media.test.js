import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({ api: vi.fn() }));

import { api } from '../api.js';
import { buildTimeline } from '../plan-timeline.js';
import {
  loadPlanPreviewMedia,
  localSecForPlayer,
  previewMediaKey,
  resolvePlanMedia,
} from '../plan-preview-media.js';

const timeline = buildTimeline([
  { index: '001', title: 'A', use_timeline: '00:10-00:15' },
  { index: '002', title: 'B', use_timeline: '00:20-00:23' },
]);
const videos = [
  { file: 'a.mp4', index: '001', offset_sec: 0 },
  { file: 'b.mp4', index: '002', offset_sec: 100 },
];

function cutPayload(overrides = {}) {
  return {
    ok: true,
    day: 'day1',
    segments: [
      {
        seg_index: 0,
        file: '001_A_seg_001.mp4',
        duration_sec: 5,
        start_sec: 10,
        end_sec: 15,
        source: { video_index: '001', timeline: '00:10-00:15' },
        ...overrides,
      },
      {
        seg_index: 1,
        file: null,
        duration_sec: null,
        start_sec: 20,
        end_sec: 23,
        source: { video_index: '002', timeline: '00:20-00:23' },
      },
    ],
  };
}

beforeEach(() => {
  sessionStorage.clear();
  api.mockReset();
});

describe('resolvePlanMedia', () => {
  it('prefers a verified cut clip and uses clip-local seek time', () => {
    const media = resolvePlanMedia(timeline, 0, {
      previewMedia: cutPayload(),
      videos,
      source: 'compressed',
      projectName: 'trip',
      localSec: 2.5,
    });
    expect(media.mode).toBe('cut');
    expect(media.seekSec).toBe(2.5);
    expect(media.endSec).toBe(5);
    expect(media.url).toContain('source=cut');
    expect(media.url).toContain('day=day1');
    expect(media.url).toContain('project=trip');
  });

  it('falls back to source when a cut is missing or explicitly blocked', () => {
    const missing = resolvePlanMedia(timeline, 1, {
      previewMedia: cutPayload(),
      videos,
      source: 'original',
      localSec: 1,
    });
    expect(missing.mode).toBe('source');
    expect(missing.seekSec).toBe(121);
    expect(missing.endSec).toBe(123);

    const blocked = resolvePlanMedia(timeline, 0, {
      previewMedia: cutPayload(),
      videos,
      source: 'compressed',
      localSec: 1,
      blockedCuts: new Set([0]),
    });
    expect(blocked.mode).toBe('source');
    expect(blocked.video.file).toBe('a.mp4');
  });

  it('rejects a stale mapping after the plan timeline changes', () => {
    const stale = cutPayload({ start_sec: 1, end_sec: 4, source: { video_index: '001' } });
    const media = resolvePlanMedia(timeline, 0, {
      previewMedia: stale,
      videos,
      source: 'compressed',
      localSec: 1,
    });
    expect(media.mode).toBe('source');
  });

  it('maps native currentTime back to segment and plan time', () => {
    const cut = resolvePlanMedia(timeline, 0, { previewMedia: cutPayload(), videos, localSec: 0 });
    expect(localSecForPlayer(timeline.segments[0], cut, 3.2)).toBe(3.2);
    const source = resolvePlanMedia(timeline, 1, {
      previewMedia: cutPayload(),
      videos,
      source: 'original',
      localSec: 0,
    });
    expect(localSecForPlayer(timeline.segments[1], source, 122)).toBe(2);
  });

  it('includes the seek target in the preload identity', () => {
    const first = resolvePlanMedia(timeline, 0, { previewMedia: cutPayload(), videos, localSec: 0 });
    const second = resolvePlanMedia(timeline, 0, { previewMedia: cutPayload(), videos, localSec: 2 });
    expect(previewMediaKey(first)).not.toBe(previewMediaKey(second));
  });
});

describe('loadPlanPreviewMedia', () => {
  it('returns null when the mapping endpoint fails', async () => {
    api.mockRejectedValueOnce(new Error('offline'));
    await expect(loadPlanPreviewMedia('day1')).resolves.toBeNull();
  });
});
