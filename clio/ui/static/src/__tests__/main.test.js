import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('application bootstrap', () => {
  it('applies a URL API token before initializing the notification center', () => {
    const source = readFileSync(resolve(process.cwd(), 'clio/ui/static/src/main.js'), 'utf8');
    const initStart = source.indexOf('async function init()');
    const tokenWrite = source.indexOf("sessionStorage.setItem('api_token', urlToken)", initStart);
    const notificationInit = source.indexOf('initNotificationCenter();', initStart);

    expect(initStart).toBeGreaterThanOrEqual(0);
    expect(tokenWrite).toBeGreaterThan(initStart);
    expect(tokenWrite).toBeLessThan(notificationInit);
  });
});
