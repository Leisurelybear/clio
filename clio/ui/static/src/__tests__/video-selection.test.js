import { describe, it, expect } from 'vitest';
import {
  findSelectedVideoIndex,
  mergeSelectedVideos,
  normPath,
} from '../video-selection.js';

describe('normPath', () => {
  it('normalizes slashes and case', () => {
    expect(normPath('D:\\GoPro\\A.MP4')).toBe('d:/gopro/a.mp4');
  });

  it('resolves . and .. segments and drops trailing separators', () => {
    expect(normPath('D:\\clips\\..\\clips\\A.MP4')).toBe('d:/clips/a.mp4');
    expect(normPath('D:/clips/./a.mp4')).toBe('d:/clips/a.mp4');
    expect(normPath('D:/clips/')).toBe('d:/clips');
    expect(normPath('')).toBe('');
  });
});

describe('findSelectedVideoIndex', () => {
  const videos = [
    'D:/old/GL010695.MP4',
    'E:/other/GL010695.MP4',
    'F:/unique/clip.mp4',
  ];

  it('prefers absolute path match', () => {
    expect(findSelectedVideoIndex(videos, {
      file: 'GL010695.MP4',
      absPath: 'E:\\other\\GL010695.MP4',
    })).toEqual({ index: 1 });
  });

  it('errors when basename alone is ambiguous', () => {
    const r = findSelectedVideoIndex(videos, { file: 'GL010695.MP4' });
    expect(r.index).toBe(-1);
    expect(r.error).toMatch(/多个同名/);
  });

  it('matches unique basename', () => {
    expect(findSelectedVideoIndex(videos, { file: 'clip.mp4' })).toEqual({ index: 2 });
  });

  it('returns not found', () => {
    const r = findSelectedVideoIndex(videos, { file: 'gone.mp4' });
    expect(r.index).toBe(-1);
    expect(r.error).toMatch(/未在项目/);
  });
});

describe('mergeSelectedVideos', () => {
  it('counts only net-new paths', () => {
    const r = mergeSelectedVideos(
      ['D:/a.mp4', 'D:/b.mp4'],
      ['D:\\b.mp4', 'D:/c.mp4', 'D:/c.mp4'],
    );
    expect(r.added).toBe(1);
    expect(r.already).toBe(2); // b once + c duplicate in candidates after first add?
    // actually: b already, c new (added++), c again already
    // Wait: second c is already in set after first c — already++
    // candidates: b(already), c(added), c(already) → added=1 already=2
    expect(r.merged).toEqual(['D:/a.mp4', 'D:/b.mp4', 'D:/c.mp4']);
  });

  it('reports zero added when all exist', () => {
    const r = mergeSelectedVideos(['D:/a.mp4'], ['D:/a.mp4']);
    expect(r.added).toBe(0);
    expect(r.already).toBe(1);
  });

  it('treats Windows paths as case-insensitive and keeps the first spelling', () => {
    const r = mergeSelectedVideos(
      ['D:/Footage/GL010695.MP4'],
      ['d:\\footage\\gl010695.mp4', 'D:/Footage/Other.MP4'],
    );
    expect(r.added).toBe(1);
    expect(r.already).toBe(1);
    expect(r.merged).toEqual(['D:/Footage/GL010695.MP4', 'D:/Footage/Other.MP4']);
  });

  it('treats .. spellings of the same file as already present', () => {
    const r = mergeSelectedVideos(['D:/clips/a.mp4'], ['D:\\clips\\..\\clips\\a.mp4']);
    expect(r.added).toBe(0);
    expect(r.already).toBe(1);
    expect(r.merged).toEqual(['D:/clips/a.mp4']);
  });
});
