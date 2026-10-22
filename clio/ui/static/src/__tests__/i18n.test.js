import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  _resetI18nForTests,
  applyLocale,
  applyTranslations,
  getLocale,
  hasKey,
  LOCALE_CHANGED_EVENT,
  loadCatalogs,
  normalizeLocale,
  onLocaleChange,
  setCatalog,
  setLocale,
  t,
} from '../i18n.js';

beforeEach(() => {
  _resetI18nForTests();
  localStorage.clear();
  document.documentElement.lang = 'zh-CN';
  document.body.innerHTML = '';
});

describe('normalizeLocale', () => {
  it('maps regional and short Chinese tags to zh-CN', () => {
    expect(normalizeLocale('zh')).toBe('zh-CN');
    expect(normalizeLocale('zh-Hans')).toBe('zh-CN');
    expect(normalizeLocale('ZH-cn')).toBe('zh-CN');
  });

  it('maps regional English tags to en and rejects unknown locales', () => {
    expect(normalizeLocale('en-US')).toBe('en');
    expect(normalizeLocale('fr')).toBe('zh-CN');
    expect(normalizeLocale('')).toBe('zh-CN');
  });
});

describe('t', () => {
  it('interpolates named parameters', () => {
    setCatalog('en', { 'notifications.unread_count': 'Notifications, {count} unread' });
    setLocale('en');
    expect(t('notifications.unread_count', { count: 3 })).toBe('Notifications, 3 unread');
  });

  it('falls back from the active locale to the bundled fallback catalogs', () => {
    setCatalog('zh-CN', { 'only.zh': '中文', 'shared': '共享' });
    setCatalog('en', { 'shared': 'Shared' });
    setLocale('en');
    expect(t('shared')).toBe('Shared');
    expect(t('only.zh')).toBe('中文');
  });

  it('returns the key when no catalog contains it', () => {
    setCatalog('en', {});
    setCatalog('zh-CN', {});
    setLocale('en');
    expect(t('missing.key')).toBe('missing.key');
  });
});

describe('hasKey', () => {
  it('reports keys from the active locale and its fallbacks', () => {
    setCatalog('zh-CN', { 'only.zh': '中文' });
    setCatalog('en', { shared: 'Shared' });

    expect(hasKey('shared', 'en')).toBe(true);
    expect(hasKey('only.zh', 'en')).toBe(true);
    expect(hasKey('missing.key', 'en')).toBe(false);
  });
});

describe('locale change notification', () => {
  it('notifies listeners and dispatches a DOM event only when the locale changes', () => {
    const listener = vi.fn();
    const events = vi.fn();
    onLocaleChange(listener);
    document.addEventListener(LOCALE_CHANGED_EVENT, events);

    setLocale('en');
    setLocale('en');

    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenCalledWith('en');
    expect(events).toHaveBeenCalledTimes(1);
    expect(events.mock.calls[0][0].detail).toEqual({ locale: 'en' });
  });

  it('stops notifying after unsubscribe', () => {
    const listener = vi.fn();
    const unsubscribe = onLocaleChange(listener);

    setLocale('en');
    unsubscribe();
    setLocale('zh-CN');

    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('keeps switching when a listener throws', () => {
    const good = vi.fn();
    onLocaleChange(() => {
      throw new Error('boom');
    });
    onLocaleChange(good);

    expect(() => setLocale('en')).not.toThrow();
    expect(getLocale()).toBe('en');
    expect(good).toHaveBeenCalledWith('en');
  });
});

describe('applyLocale', () => {
  it('switches locale and re-applies static markup', () => {
    setCatalog('en', { 'app.title': 'Vlog Editing UI' });
    document.body.innerHTML = '<span id="text" data-i18n="app.title">Vlog 剪辑 UI</span>';

    applyLocale('en');

    expect(getLocale()).toBe('en');
    expect(document.getElementById('text').textContent).toBe('Vlog Editing UI');
    expect(document.documentElement.lang).toBe('en');
  });
});

describe('applyTranslations', () => {
  it('updates text, titles, aria labels, and placeholders', () => {
    setCatalog('en', {
      'app.title': 'Vlog Editing UI',
      'common.save': 'Save',
      'common.search': 'Search',
    });
    setLocale('en');
    document.body.innerHTML = `
      <span id="text" data-i18n="app.title"></span>
      <button id="title" data-i18n-title="common.save"></button>
      <button id="aria" data-i18n-aria-label="common.save"></button>
      <input id="placeholder" data-i18n-placeholder="common.search">
    `;

    expect(applyTranslations(document)).toBe(4);
    expect(document.getElementById('text').textContent).toBe('Vlog Editing UI');
    expect(document.getElementById('title').title).toBe('Save');
    expect(document.getElementById('aria').getAttribute('aria-label')).toBe('Save');
    expect(document.getElementById('placeholder').placeholder).toBe('Search');
    expect(document.documentElement.lang).toBe('en');
  });
});

describe('loadCatalogs', () => {
  it('loads both catalogs through the static asset URL', async () => {
    const fetchImpl = vi.fn(async url => ({
      ok: true,
      json: async () => url.includes('/en.json')
        ? { 'app.title': 'Vlog Editing UI' }
        : { 'app.title': 'Vlog 剪辑 UI' },
    }));

    expect(await loadCatalogs({ fetchImpl })).toBe(2);
    expect(fetchImpl).toHaveBeenCalledWith('/static/locales/zh-CN.json');
    expect(fetchImpl).toHaveBeenCalledWith('/static/locales/en.json');
    setLocale('en');
    expect(t('app.title')).toBe('Vlog Editing UI');
    expect(getLocale()).toBe('en');
  });

  it('keeps booting with key fallback when a catalog request fails', async () => {
    const fetchImpl = vi.fn(async () => ({ ok: false }));
    expect(await loadCatalogs({ fetchImpl })).toBe(0);
    setLocale('en');
    expect(t('app.title')).toBe('app.title');
  });
});
