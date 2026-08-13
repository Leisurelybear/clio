import { describe, it, expect } from 'vitest';
import { stripQueryParams } from '../url-params.js';

describe('stripQueryParams', () => {
  it('removes only listed keys and keeps others', () => {
    expect(stripQueryParams('?token=abc&project=day1&project_dir=%2Fa', ['token']))
      .toBe('?project=day1&project_dir=%2Fa');
  });

  it('returns empty when nothing remains', () => {
    expect(stripQueryParams('?token=abc', ['token'])).toBe('');
  });

  it('handles missing leading ?', () => {
    expect(stripQueryParams('token=x&keep=1', ['token'])).toBe('?keep=1');
  });
});
