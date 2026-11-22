import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { icon } from '../api.js';

const SRC_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

function usedIconNames() {
  const names = new Set();
  for (const file of readdirSync(SRC_ROOT)) {
    if (!file.endsWith('.js')) continue;
    const source = readFileSync(join(SRC_ROOT, file), 'utf8');
    for (const match of source.matchAll(/\bicon\('([^']+)'/g)) names.add(match[1]);
  }
  return [...names];
}

describe('icon names', () => {
  it('every literal icon() name used in the UI exists in the icon set', () => {
    const fallback = icon('__missing__');
    const unknown = usedIconNames().filter(name => name !== 'file' && icon(name) === fallback);
    expect(unknown).toEqual([]);
  });

  it('has a dedicated download icon', () => {
    expect(icon('download')).not.toBe(icon('__missing__'));
  });
});
