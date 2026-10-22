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
        rev: '123-456',
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
    expect(media.url).toContain('rev=123-456');
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

  it('returns null without a day label', async () => {
    await expect(loadPlanPreviewMedia('')).resolves.toBeNull();
    expect(api).not.toHaveBeenCalled();
  });
});

describe('resolvePlanMedia edge cases', () => {
  it('returns null for a missing segment or unmatched video', () => {
    expect(resolvePlanMedia(timeline, 99, { videos, localSec: 0 })).toBeNull();
    expect(resolvePlanMedia(timeline, 0, { videos: [], localSec: 0 })).toBeNull();
    expect(resolvePlanMedia(null, 0)).toBeNull();
  });

  it('honours an array of blocked cut indices', () => {
    const media = resolvePlanMedia(timeline, 0, {
      previewMedia: cutPayload(),
      videos,
      source: 'compressed',
      localSec: 1,
      blockedCuts: [0],
    });
    expect(media.mode).toBe('source');
  });

  it('accepts a clip without a source index and rejects a mismatched end', () => {
    const noSource = cutPayload({ source: undefined });
    expect(resolvePlanMedia(timeline, 0, { previewMedia: noSource, videos, localSec: 1 }).mode).toBe('cut');

    const badEnd = cutPayload({ end_sec: 999 });
    expect(resolvePlanMedia(timeline, 0, { previewMedia: badEnd, videos, localSec: 1 }).mode).toBe('source');

    const wrongIndex = cutPayload({ source: { video_index: '999' } });
    expect(resolvePlanMedia(timeline, 0, { previewMedia: wrongIndex, videos, localSec: 1 }).mode).toBe('source');
  });

  it('clamps non-finite local time and tolerates empty inputs', () => {
    expect(localSecForPlayer(null, { mode: 'cut' }, 5)).toBe(0);
    expect(previewMediaKey(null)).toBe('');
    const cut = resolvePlanMedia(timeline, 0, {
      previewMedia: cutPayload(),
      videos,
      localSec: Number.NaN,
    });
    expect(cut.seekSec).toBe(0);
  });

  it('skips URL building when buildUrl is false and omits the project', () => {
    const cut = resolvePlanMedia(timeline, 0, {
      previewMedia: cutPayload(),
      videos,
      buildUrl: false,
      localSec: 1,
    });
    expect(cut.url).toBe('');
    expect(cut.projectName).toBeNull();

    const source = resolvePlanMedia(timeline, 1, {
      previewMedia: cutPayload(),
      videos,
      buildUrl: false,
      source: 'original',
      localSec: 1,
    });
    expect(source.url).toBe('');
  });
});
