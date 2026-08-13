import { describe, it, expect } from 'vitest';
import { beginLatest, isLatest, endLatest, isAbortError } from '../latest.js';

describe('beginLatest / isLatest (P2-P37)', () => {
  it('aborts the previous controller when starting a new one', () => {
    const first = beginLatest('videos');
    expect(first.signal.aborted).toBe(false);
    const second = beginLatest('videos');
    expect(first.signal.aborted).toBe(true);
    expect(second.signal.aborted).toBe(false);
    expect(isLatest('videos', first)).toBe(false);
    expect(isLatest('videos', second)).toBe(true);
    endLatest('videos', second);
  });

  it('endLatest only clears matching controller', () => {
    const first = beginLatest('plans');
    const second = beginLatest('plans');
    endLatest('plans', first);
    expect(isLatest('plans', second)).toBe(true);
    endLatest('plans', second);
    expect(isLatest('plans', second)).toBe(false);
  });

  it('isAbortError detects AbortError', () => {
    const err = new DOMException('aborted', 'AbortError');
    expect(isAbortError(err)).toBe(true);
    expect(isAbortError(new Error('other'))).toBe(false);
  });
});
