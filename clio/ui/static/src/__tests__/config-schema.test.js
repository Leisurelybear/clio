import { beforeEach, describe, it, expect, vi } from 'vitest';

vi.mock('../api.js', async () => {
  const actual = await vi.importActual('../api.js');
  return { ...actual, api: vi.fn() };
});

const { _isAdvancedVisible, _setAdvancedVisible } = await import('../editor-config.js');

beforeEach(() => {
  localStorage.clear();
});

describe('advanced toggle persistence', () => {
  it('defaults to false', () => {
    expect(_isAdvancedVisible()).toBe(false);
  });

  it('persists true after setAdvancedVisible(true)', () => {
    _setAdvancedVisible(true);
    expect(_isAdvancedVisible()).toBe(true);
    expect(localStorage.getItem('vlog-config-show-advanced')).toBe('true');
  });

  it('persists false after setAdvancedVisible(false)', () => {
    _setAdvancedVisible(true);
    _setAdvancedVisible(false);
    expect(_isAdvancedVisible()).toBe(false);
  });
});
