import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../video-url.js', () => ({
  videoUrlFor: vi.fn(
    (v, source, projectName) =>
      '/api/video?file=' + encodeURIComponent(v.file) + '&source=' + source +
      (projectName ? '&project=' + encodeURIComponent(projectName) : ''),
  ),
}));

import { preloadNextSegment, clearPreload } from '../preview-preload.js';
import { videoUrlFor } from '../video-url.js';

let mockVideo;

beforeEach(() => {
  vi.useFakeTimers();
  window.HTMLMediaElement.prototype.pause = vi.fn();
  window.HTMLMediaElement.prototype.load = vi.fn();
  document.body.innerHTML = '';
  clearPreload();
});

afterEach(() => {
  vi.useRealTimers();
});

function getCreated() {
  return document.querySelector('body > video');
}

function makeTimeline(segs) {
  return { segments: segs };
}

const nextFn = (tl, from) => {
  const idx = from + 1;
  return idx < tl.segments.length ? idx : null;
};

describe('preloadNextSegment', () => {
  it('sets hidden video src to the next playable segment URL', () => {
    const tl = makeTimeline([
      { videoIndex: '1', planStart: 0 },
      { videoIndex: '2', planStart: 10 },
    ]);
    const videos = [
      { file: 'a.mp4', index: '1' },
      { file: 'b.mp4', index: '2' },
    ];
    preloadNextSegment(true, tl, 0, nextFn, 'compressed', videos, null);
    vi.advanceTimersByTime(250);
    expect(videoUrlFor).toHaveBeenCalledWith(videos[1], 'compressed', null);
    expect(getCreated()?.src).toContain('b.mp4');
  });

  it('does not preload when previewActive is false', () => {
    const tl = makeTimeline([{ videoIndex: '1' }, { videoIndex: '2' }]);
    preloadNextSegment(false, tl, 0, nextFn, 'compressed', [], null);
    vi.advanceTimersByTime(250);
    expect(document.querySelector('video')).toBeNull();
  });

  it('debounces rapid calls and only fires once', async () => {
    // Ensure clean module state by clearing any previous preload
    clearPreload();
    vi.clearAllMocks();
    const tl = makeTimeline([{ videoIndex: '1' }, { videoIndex: '2' }]);
    const videos = [{ file: 'a.mp4', index: '1' }, { file: 'b.mp4', index: '2' }];
    preloadNextSegment(true, tl, 0, nextFn, 'compressed', videos, null);
    preloadNextSegment(true, tl, 0, nextFn, 'compressed', videos, null);
    preloadNextSegment(true, tl, 0, nextFn, 'compressed', videos, null);
    vi.advanceTimersByTime(250);
    expect(videoUrlFor).toHaveBeenCalledTimes(1);
  });

  it('deduplicates same key across calls', async () => {
    clearPreload();
    vi.clearAllMocks();
    const tl = makeTimeline([{ videoIndex: '1' }, { videoIndex: '2' }]);
    const videos = [{ file: 'a.mp4', index: '1' }, { file: 'b.mp4', index: '2' }];
    preloadNextSegment(true, tl, 0, nextFn, 'compressed', videos, null);
    vi.advanceTimersByTime(250);
    // Second call with same params should not re-set src
    const firstSrc = getCreated()?.src;
    preloadNextSegment(true, tl, 0, nextFn, 'compressed', videos, null);
    vi.advanceTimersByTime(250);
    expect(getCreated()?.src).toBe(firstSrc);
    expect(videoUrlFor).toHaveBeenCalledTimes(1);
  });

  it('reloads when source changes for the same file', () => {
    vi.clearAllMocks();
    const v = { file: 'a.mp4', abs_path: '/x/a.mp4', index: '1' };
    const tl = makeTimeline([{ videoIndex: '1' }, { videoIndex: '1' }]);
    preloadNextSegment(true, tl, 0, nextFn, 'compressed', [v], null);
    vi.advanceTimersByTime(250);
    preloadNextSegment(true, tl, 0, nextFn, 'original', [v], null);
    vi.advanceTimersByTime(250);
    expect(videoUrlFor).toHaveBeenCalledTimes(2);
  });

  it('skips when no next segment exists', () => {
    const tl = makeTimeline([{ videoIndex: '1' }]);
    preloadNextSegment(true, tl, 0, (t, f) => null, 'compressed', [{ file: 'a.mp4', index: '1' }], null);
    vi.advanceTimersByTime(250);
    expect(document.querySelector('video')).toBeNull();
  });
});

describe('clearPreload', () => {
  it('clears state so next preload re-fires', () => {
    clearPreload();
    vi.clearAllMocks();
    const tl = makeTimeline([{ videoIndex: '1' }, { videoIndex: '2' }]);
    const videos = [{ file: 'a.mp4', index: '1' }, { file: 'b.mp4', index: '2' }];
    preloadNextSegment(true, tl, 0, nextFn, 'compressed', videos, null);
    vi.advanceTimersByTime(250);
    expect(videoUrlFor).toHaveBeenCalledTimes(1);

    // After clear, same call should trigger a new preload (new key)
    clearPreload();
    preloadNextSegment(true, tl, 0, nextFn, 'compressed', videos, null);
    vi.advanceTimersByTime(250);
    expect(videoUrlFor).toHaveBeenCalledTimes(2);
  });

  it('is safe when called without prior preload', () => {
    expect(() => clearPreload()).not.toThrow();
  });
});
