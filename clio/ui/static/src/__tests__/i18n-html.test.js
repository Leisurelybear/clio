import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import en from '../../locales/en.json';
import zhCN from '../../locales/zh-CN.json';

const htmlPath = resolve(dirname(fileURLToPath(import.meta.url)), '../../index.html');
const html = readFileSync(htmlPath, 'utf8');

const ATTRS = ['data-i18n', 'data-i18n-title', 'data-i18n-aria-label', 'data-i18n-placeholder'];

function attributeKeys(name) {
  const re = new RegExp(`${name}="([^"]+)"`, 'g');
  const keys = [];
  let match;
  while ((match = re.exec(html))) keys.push(match[1]);
  return keys;
}

describe('index.html i18n bindings', () => {
  it('binds the static chrome copy', () => {
    expect(attributeKeys('data-i18n').length).toBeGreaterThan(30);
  });

  it('resolves every binding in both shipped catalogs', () => {
    for (const attr of ATTRS) {
      for (const key of attributeKeys(attr)) {
        expect(zhCN[key], `zh-CN missing ${key}`).toBeTruthy();
        expect(en[key], `en missing ${key}`).toBeTruthy();
      }
    }
  });

  it('keeps Chinese copy bound to a data-i18n attribute', () => {
    const lines = html.split('\n');
    const offenders = [];
    lines.forEach((line, index) => {
      if (!/[\u4e00-\u9fff]/.test(line)) return;
      const window = [lines[index - 1] || '', line, lines[index + 1] || ''].join('\n');
      if (!window.includes('data-i18n')) offenders.push(`${index + 1}: ${line.trim()}`);
    });
    expect(offenders).toEqual([]);
  });
});
