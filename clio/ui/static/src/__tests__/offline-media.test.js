import { describe, it, expect } from 'vitest';
import {
  summarizeOfflineVideos,
  matchBatchRelink,
  basenameLower,
} from '../offline-media.js';

describe('basenameLower', () => {
  it('extracts basename case-insensitively', () => {
    expect(basenameLower('D:\\GoPro\\GL010695.MP4')).toBe('gl010695.mp4');
    expect(basenameLower('/home/u/GL010695.mp4')).toBe('gl010695.mp4');
  });
});

describe('summarizeOfflineVideos', () => {
  it('counts missing videos and returns their display names', () => {
    const s = summarizeOfflineVideos([
      { file: '001_a.mp4', missing: true, abs_path: 'D:/old/a.mp4' },
      { file: '002_b.mp4', missing: false },
      { file: '003_c.mp4', missing: true, abs_path: 'D:/old/c.mp4' },
    ]);
    expect(s.count).toBe(2);
    expect(s.items.map(i => i.file)).toEqual(['001_a.mp4', '003_c.mp4']);
    expect(s.items[0].abs_path).toBe('D:/old/a.mp4');
  });

  it('returns zero when none offline', () => {
    expect(summarizeOfflineVideos([{ file: 'x.mp4' }])).toEqual({ count: 0, items: [] });
  });
});

describe('matchBatchRelink', () => {
  const offline = [
    { file: '001_GL010695.MP4', abs_path: 'D:/old/GL010695.MP4' },
    { file: '002_GL010696.MP4', abs_path: 'E:/gone/GL010696.MP4' },
    { file: '003_unique.MP4', abs_path: 'D:/old/unique.MP4' },
  ];

  it('matches candidates by basename ignoring case and index prefix in display', () => {
    const candidates = [
      { path: 'F:/new/GL010695.MP4', name: 'GL010695.MP4' },
      { path: 'F:/new/gl010696.mp4', name: 'gl010696.mp4' },
    ];
    const r = matchBatchRelink(offline, candidates);
    expect(r.matched).toHaveLength(2);
    expect(r.matched[0]).toMatchObject({
      old_path: 'D:/old/GL010695.MP4',
      new_path: 'F:/new/GL010695.MP4',
      file: '001_GL010695.MP4',
    });
    expect(r.matched[1].new_path.toLowerCase()).toContain('gl010696');
    expect(r.unmatched.map(u => u.file)).toEqual(['003_unique.MP4']);
  });

  it('skips ambiguous basenames when multiple candidates share the same name', () => {
    const candidates = [
      { path: 'F:/a/GL010695.MP4', name: 'GL010695.MP4' },
      { path: 'F:/b/GL010695.MP4', name: 'GL010695.MP4' },
    ];
    const r = matchBatchRelink(offline, candidates);
    expect(r.matched.find(m => m.file.includes('GL010695'))).toBeUndefined();
    expect(r.ambiguous.map(a => a.basename)).toContain('gl010695.mp4');
  });

  it('uses file as old_path when abs_path is missing', () => {
    const r = matchBatchRelink(
      [{ file: 'clip.mp4' }],
      [{ path: 'D:/here/clip.mp4', name: 'clip.mp4' }],
    );
    expect(r.matched[0].old_path).toBe('clip.mp4');
    expect(r.matched[0].new_path).toBe('D:/here/clip.mp4');
  });

  it('assigns each candidate path to at most one offline item', () => {
    // Two project rows normalize to the same stem; only one file on disk.
    const r = matchBatchRelink(
      [
        { file: '001_GL.MP4', abs_path: 'D:/old/GL.MP4' },
        { file: 'GL.MP4', abs_path: 'E:/other/GL.MP4' },
      ],
      [{ path: 'F:/new/GL.MP4', name: 'GL.MP4' }],
    );
    expect(r.matched).toHaveLength(1);
    expect(r.matched[0].new_path).toBe('F:/new/GL.MP4');
    expect(r.unmatched).toHaveLength(1);
  });

  it('prefers an exact indexed filename over its stripped stem', () => {
    const r = matchBatchRelink(
      [{ file: '001_GL.MP4', abs_path: 'D:/old/001_GL.MP4' }],
      [
        { path: 'F:/new/GL.MP4', name: 'GL.MP4' },
        { path: 'F:/new/001_GL.MP4', name: '001_GL.MP4' },
      ],
    );
    expect(r.ambiguous).toEqual([]);
    expect(r.matched[0].new_path).toBe('F:/new/001_GL.MP4');
  });

  it('uses the original abs_path filename before a display-index prefix', () => {
    const r = matchBatchRelink(
      [{ file: '001_GL.MP4', abs_path: 'D:/old/other.mov' }],
      [
        { path: 'F:/new/001_GL.MP4', name: '001_GL.MP4' },
        { path: 'F:/new/other.mov', name: 'other.mov' },
      ],
    );
    expect(r.ambiguous).toEqual([]);
    expect(r.matched[0].new_path).toBe('F:/new/other.mov');
  });
});
