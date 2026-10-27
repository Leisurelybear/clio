import { describe, it, expect } from 'vitest';
import {
  findSelectedVideoIndex,
  mergeSelectedVideos,
  normPath,
  removeSelectedVideos,
  toggleSelectionFor,
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


describe('removeSelectedVideos', () => {
  const videos = [
    'D:/clips/001_a.mp4',
    'D:/clips/002_b.mp4',
    'D:/clips/003_c.mp4',
  ];

  it('removes several targets by absolute path in one pass', () => {
    const r = removeSelectedVideos(videos, [
      { file: 'a.mp4', absPath: 'D:/clips/001_a.mp4' },
      { file: 'c.mp4', absPath: 'D:/clips/003_c.mp4' },
    ]);
    expect(r.removed).toEqual(['D:/clips/001_a.mp4', 'D:/clips/003_c.mp4']);
    expect(r.remaining).toEqual(['D:/clips/002_b.mp4']);
    expect(r.missing).toEqual([]);
    expect(r.ambiguous).toEqual([]);
  });

  it('falls back to a unique basename when no absolute path is given', () => {
    const r = removeSelectedVideos(videos, [{ file: 'b.mp4' }]);
    expect(r.removed).toEqual(['D:/clips/002_b.mp4']);
    expect(r.remaining).toEqual(['D:/clips/001_a.mp4', 'D:/clips/003_c.mp4']);
  });

  it('reports missing entries without removing anything', () => {
    const r = removeSelectedVideos(videos, [{ file: 'gone.mp4' }]);
    expect(r.removed).toEqual([]);
    expect(r.remaining).toEqual(videos);
    expect(r.missing).toEqual(['gone.mp4']);
  });

  it('reports ambiguous basenames separately from missing', () => {
    const dup = ['D:/a/same.mp4', 'D:/b/same.mp4'];
    const r = removeSelectedVideos(dup, [{ file: 'same.mp4' }]);
    expect(r.removed).toEqual([]);
    expect(r.ambiguous).toHaveLength(1);
    expect(r.ambiguous[0]).toMatch(/多个同名/);
    expect(r.missing).toEqual([]);
  });

  it('dedupes repeated targets and ignores empty values', () => {
    const r = removeSelectedVideos(videos, [
      { file: 'a.mp4', absPath: 'D:/clips/001_a.mp4' },
      { file: 'a.mp4', absPath: 'D:/clips/001_a.mp4' },
      null,
      {},
    ]);
    expect(r.removed).toEqual(['D:/clips/001_a.mp4']);
    expect(r.missing).toEqual([]);
  });
});

describe('toggleSelectionFor', () => {
  it('selects only the provided subset and keeps earlier selections', () => {
    expect(toggleSelectionFor(['001_a.mp4', '002_b.mp4'], ['003_c.mp4', '004_d.mp4'])).toEqual([
      '001_a.mp4',
      '002_b.mp4',
      '003_c.mp4',
      '004_d.mp4',
    ]);
  });

  it('deselects only the provided subset when all of it is selected', () => {
    expect(
      toggleSelectionFor(['001_a.mp4', '002_b.mp4', '003_c.mp4'], ['002_b.mp4', '003_c.mp4'])
    ).toEqual(['001_a.mp4']);
  });

  it('adds the missing subset entries when only some are selected', () => {
    expect(toggleSelectionFor(['002_b.mp4'], ['002_b.mp4', '003_c.mp4'])).toEqual([
      '002_b.mp4',
      '003_c.mp4',
    ]);
  });

  it('is a no-op for an empty subset and never duplicates entries', () => {
    expect(toggleSelectionFor(['001_a.mp4'], [])).toEqual(['001_a.mp4']);
    expect(toggleSelectionFor(['001_a.mp4'], ['001_a.mp4', '002_b.mp4'])).toEqual([
      '001_a.mp4',
      '002_b.mp4',
    ]);
  });
});
