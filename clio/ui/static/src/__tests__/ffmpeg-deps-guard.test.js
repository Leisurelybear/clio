import { describe, it, expect } from 'vitest';
import { mediaStepsNeedFfmpeg, missingMediaDepsForSteps } from '../runner.js';

describe('mediaStepsNeedFfmpeg', () => {
  it('true for compress/label/transcribe', () => {
    expect(mediaStepsNeedFfmpeg(['analyze', 'compress'])).toBe(true);
    expect(mediaStepsNeedFfmpeg(['label'])).toBe(true);
    expect(mediaStepsNeedFfmpeg(['transcribe'])).toBe(true);
  });
  it('false for analyze/voiceover/plan only', () => {
    expect(mediaStepsNeedFfmpeg(['analyze', 'voiceover', 'plan'])).toBe(false);
  });
  it('false for empty', () => {
    expect(mediaStepsNeedFfmpeg([])).toBe(false);
    expect(mediaStepsNeedFfmpeg(null)).toBe(false);
  });
});

describe('missingMediaDepsForSteps', () => {
  it('does not block a label run when only ffprobe is missing', () => {
    expect(missingMediaDepsForSteps(['label'], { missing: ['ffprobe'] })).toEqual([]);
  });

  it('keeps only dependencies required by the selected steps', () => {
    expect(missingMediaDepsForSteps(['analyze', 'transcribe'], {
      missing: ['ffmpeg', 'ffprobe', 'other'],
    })).toEqual(['ffmpeg', 'ffprobe']);
  });

  it('does not make an unknown or text-only step media-blocked', () => {
    expect(missingMediaDepsForSteps(['analyze', 'voiceover', 'plan'], {
      missing: ['ffmpeg', 'ffprobe'],
    })).toEqual([]);
  });
});
