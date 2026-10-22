import { describe, it, expect } from 'vitest';
import { playerSrcMatchesFile } from '../viewer.js';

describe('playerSrcMatchesFile', () => {
  it('matches exact file query', () => {
    const src = 'http://127.0.0.1:8765/api/video?file=vid.mp4&source=compressed';
    expect(playerSrcMatchesFile(src, 'vid.mp4', 'compressed')).toBe(true);
  });

  it('rejects basename substring false positive', () => {
    const src = 'http://127.0.0.1:8765/api/video?file=other_vid.mp4&source=compressed';
    expect(playerSrcMatchesFile(src, 'vid.mp4', 'compressed')).toBe(false);
  });

  it('rejects different source', () => {
    const src = '/api/video?file=vid.mp4&source=original';
    expect(playerSrcMatchesFile(src, 'vid.mp4', 'compressed')).toBe(false);
  });

  it('handles encoded filenames', () => {
    const name = '街 头.mp4';
    const src = `/api/video?file=${encodeURIComponent(name)}&source=compressed`;
    expect(playerSrcMatchesFile(src, name, 'compressed')).toBe(true);
  });

  it('requires the same cut day and file revision', () => {
    const src = '/api/video?file=clip.mp4&source=cut&day=day1&rev=10-20';
    expect(playerSrcMatchesFile(src, 'clip.mp4', 'cut', { day: 'day1', rev: '10-20' })).toBe(true);
    expect(playerSrcMatchesFile(src, 'clip.mp4', 'cut', { day: 'day2', rev: '10-20' })).toBe(false);
    expect(playerSrcMatchesFile(src, 'clip.mp4', 'cut', { day: 'day1', rev: '10-21' })).toBe(false);
  });

  it('requires the same project and absolute source identity', () => {
    const src = '/api/video?file=clip.mp4&source=original&abspath=C%3A%2Fv%2Fclip.mp4&project=trip';
    const identity = { project: 'trip', abspath: 'C:/v/clip.mp4' };
    expect(playerSrcMatchesFile(src, 'clip.mp4', 'original', identity)).toBe(true);
    expect(playerSrcMatchesFile(src, 'clip.mp4', 'original', { ...identity, project: 'other' })).toBe(false);
    expect(playerSrcMatchesFile(src, 'clip.mp4', 'original', { ...identity, abspath: 'C:/v/other.mp4' })).toBe(false);
  });
});
