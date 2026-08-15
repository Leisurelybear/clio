import { describe, it, expect } from 'vitest';
import { resolveSessionRestore, buildProjectSavePayload } from '../session-restore.js';

describe('buildProjectSavePayload', () => {
  const base = {
    currentDay: 'day1',
    source: 'compressed',
    currentEntity: 'video',
    currentVideo: '003_c.mp4',
    projectName: 'Trip',
  };

  it('writes lastEntity and lastVideo when a video is selected', () => {
    expect(buildProjectSavePayload(base)).toEqual({
      currentDay: 'day1',
      source: 'compressed',
      lastEntity: 'video',
      lastVideo: '003_c.mp4',
      name: 'Trip',
    });
  });

  it('omits lastVideo when currentVideo is null so PUT merge keeps the previous value', () => {
    const payload = buildProjectSavePayload({
      ...base,
      currentEntity: 'run',
      currentVideo: null,
      projectName: '',
    });
    expect(payload).toEqual({
      currentDay: 'day1',
      source: 'compressed',
      lastEntity: 'run',
    });
    expect(payload).not.toHaveProperty('lastVideo');
    expect(payload).not.toHaveProperty('name');
  });

  it('allows explicit lastVideo override via extra (including null clear)', () => {
    expect(buildProjectSavePayload(
      { ...base, currentVideo: null },
      { lastVideo: 'kept.mp4' },
    )).toMatchObject({ lastVideo: 'kept.mp4', lastEntity: 'video' });

    expect(buildProjectSavePayload(base, { lastVideo: null })).toMatchObject({
      lastVideo: null,
    });
  });
});

describe('resolveSessionRestore', () => {
  const videos = [
    { file: '001_a.mp4' },
    { file: '002_b.mp4', missing: true },
    { file: '003_c.mp4' },
  ];

  it('restores last video entity when lastVideo is still available', () => {
    expect(resolveSessionRestore({
      lastEntity: 'video',
      lastVideo: '003_c.mp4',
      videos,
    })).toEqual({ entity: 'video', video: '003_c.mp4' });
  });

  it('falls back to first non-missing video when lastVideo is missing', () => {
    expect(resolveSessionRestore({
      lastEntity: 'video',
      lastVideo: '002_b.mp4',
      videos,
    })).toEqual({ entity: 'video', video: '001_a.mp4' });
  });

  it('falls back to first non-missing video when lastVideo is unknown', () => {
    expect(resolveSessionRestore({
      lastEntity: 'video',
      lastVideo: '999_gone.mp4',
      videos,
    })).toEqual({ entity: 'video', video: '001_a.mp4' });
  });

  it('restores plan/run/config/logs/tokens/tasks without requiring a video', () => {
    for (const entity of ['plan', 'run', 'config', 'logs', 'tokens', 'tasks']) {
      expect(resolveSessionRestore({
        lastEntity: entity,
        lastVideo: '003_c.mp4',
        videos,
      })).toEqual({ entity, video: '003_c.mp4' });
    }
  });

  it('defaults to first available video when lastEntity is absent', () => {
    expect(resolveSessionRestore({
      lastEntity: null,
      lastVideo: null,
      videos,
    })).toEqual({ entity: 'video', video: '001_a.mp4' });
  });

  it('returns no video when list is empty', () => {
    expect(resolveSessionRestore({
      lastEntity: 'plan',
      lastVideo: 'x.mp4',
      videos: [],
    })).toEqual({ entity: 'plan', video: null });
  });

  it('ignores invalid lastEntity values', () => {
    expect(resolveSessionRestore({
      lastEntity: 'nope',
      lastVideo: '003_c.mp4',
      videos,
    })).toEqual({ entity: 'video', video: '003_c.mp4' });
  });
});
