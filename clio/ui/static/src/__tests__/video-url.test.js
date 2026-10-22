import { beforeEach, describe, expect, it } from 'vitest';

import { videoUrlFor } from '../video-url.js';

describe('videoUrlFor', () => {
  beforeEach(() => sessionStorage.clear());

  it('builds a source URL with abs path, project, and token', () => {
    sessionStorage.setItem('api_token', 'tok en');
    const url = videoUrlFor({ file: 'a b.mp4', abs_path: '/x/y.mp4' }, 'original', 'Paris');
    expect(url).toBe(
      '/api/video?file=a%20b.mp4&source=original&abspath=%2Fx%2Fy.mp4&project=Paris&token=tok%20en',
    );
  });

  it('builds a cut URL with day and revision', () => {
    const url = videoUrlFor({ file: 'c.mp4', cut_day: 'day1', rev: 'r2' }, 'cut', null);
    expect(url).toBe('/api/video?file=c.mp4&source=cut&day=day1&rev=r2');
  });

  it('omits every optional param when absent', () => {
    expect(videoUrlFor({ file: 'd.mp4' }, 'compressed', null)).toBe('/api/video?file=d.mp4&source=compressed');
    expect(videoUrlFor({ file: 'e.mp4' }, 'cut', null)).toBe('/api/video?file=e.mp4&source=cut');
  });
});
