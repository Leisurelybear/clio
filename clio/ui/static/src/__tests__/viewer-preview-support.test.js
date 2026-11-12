import { beforeEach, describe, expect, it } from 'vitest';
import { state } from '../state.js';
import { planIndexesKey } from '../viewer-preview-support.js';

beforeEach(() => {
  state.source = 'compressed';
});

describe('planIndexesKey', () => {
  it('deduplicates and sorts timeline video indexes for the active source', () => {
    state.source = 'original';
    const tl = {
      segments: [
        { videoIndex: '002' },
        { videoIndex: '001' },
        { videoIndex: '002' },
        { videoIndex: '' },
      ],
    };

    expect(planIndexesKey(tl)).toBe('original|001,002');
  });

  it('falls back to compressed and handles a missing timeline', () => {
    state.source = undefined;

    expect(planIndexesKey(null)).toBe('compressed|');
  });
});
