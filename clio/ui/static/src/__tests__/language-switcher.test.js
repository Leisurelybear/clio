import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({ api: vi.fn() }));
vi.mock('../toast.js', () => ({ addToast: vi.fn() }));

import { api } from '../api.js';
import { addToast } from '../toast.js';
import { state } from '../state.js';
import { _resetI18nForTests, applyLocale, getLocale } from '../i18n.js';
import { seedCatalogs } from './catalogs.js';
import {
  DEFAULT_DESKTOP_NOTIFICATIONS,
  SUPPORTED_LOCALES,
  ensureGlobalUiDefaults,
  initLanguageSwitcher,
  languageButtonLabel,
  nextLocale,
  switchLanguage,
} from '../language-switcher.js';

const ZH_LABEL = '\u4e2d';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function mountHeader() {
  document.body.innerHTML = '<button id="btn-lang"><span id="btn-lang-label"></span></button>';
  return document;
}

function stubConfigApi({ get = {}, put = { ok: true } } = {}) {
  api.mockImplementation(async (method) => (method === 'GET' ? get : put));
}

function putCalls() {
  return api.mock.calls.filter((call) => call[0] === 'PUT');
}

beforeEach(() => {
  api.mockReset();
  addToast.mockReset();
  state.configGlobal = null;
  state.dirty = false;
  _resetI18nForTests();
  seedCatalogs();
  applyLocale('zh-CN');
});

afterEach(() => {
  document.body.replaceChildren();
});

describe('header language button labels', () => {
  it('labels the button with the locale the user is currently on', () => {
    expect(languageButtonLabel('zh-CN')).toBe(ZH_LABEL);
    expect(languageButtonLabel('en')).toBe('EN');
  });

  it('falls back to the active locale when no locale is given', () => {
    applyLocale('en');
    expect(languageButtonLabel()).toBe('EN');
    applyLocale('zh-CN');
    expect(languageButtonLabel()).toBe(ZH_LABEL);
    expect(languageButtonLabel('fr')).toBe(ZH_LABEL);
  });

  it('cycles through every supported locale and wraps around', () => {
    expect(SUPPORTED_LOCALES).toEqual(['zh-CN', 'en']);
    expect(nextLocale('zh-CN')).toBe('en');
    expect(nextLocale('en')).toBe('zh-CN');
    expect(nextLocale('fr')).toBe('en');
  });
});

describe('ensureGlobalUiDefaults', () => {
  it('adds the missing ui block and keeps the rest of the config', () => {
    const global = ensureGlobalUiDefaults({ paths: { output_dir: 'out' } }, 'en');

    expect(global.ui).toEqual({ language: 'en', desktop_notifications: DEFAULT_DESKTOP_NOTIFICATIONS });
    expect(global.paths).toEqual({ output_dir: 'out' });
  });

  it('keeps existing ui values, including extra keys', () => {
    const global = ensureGlobalUiDefaults({ ui: { language: 'en', desktop_notifications: true, extra: 1 } }, 'zh-CN');

    expect(global.ui).toEqual({ language: 'en', desktop_notifications: true, extra: 1 });
  });

  it('repairs a non-boolean flag and a non-object ui block', () => {
    expect(ensureGlobalUiDefaults({ ui: { desktop_notifications: 'yes' } }, 'en').ui.desktop_notifications)
      .toBe(false);
    expect(ensureGlobalUiDefaults({ ui: 'broken' }, 'en').ui.language).toBe('en');
  });

  it('mutates and returns the same object, and ignores non-objects', () => {
    const global = {};
    expect(ensureGlobalUiDefaults(global, 'en')).toBe(global);
    expect(ensureGlobalUiDefaults(null)).toBeNull();
    expect(ensureGlobalUiDefaults(undefined)).toBeUndefined();
    expect(ensureGlobalUiDefaults('x')).toBe('x');
  });
});

describe('switchLanguage', () => {
  it('applies the next locale and persists it into the global config', async () => {
    stubConfigApi({ get: { paths: { output_dir: 'out' } } });

    const result = await switchLanguage();

    expect(result).toEqual({ ok: true, locale: 'en', persisted: true });
    expect(getLocale()).toBe('en');
    expect(api.mock.calls[0][0]).toBe('GET');
    expect(putCalls()).toHaveLength(1);
    expect(putCalls()[0][1]).toBe('/api/config/global');
    expect(putCalls()[0][2]).toEqual({
      paths: { output_dir: 'out' },
      ui: { language: 'en', desktop_notifications: DEFAULT_DESKTOP_NOTIFICATIONS },
    });
  });

  it('reuses the loaded form state while the config form is clean', async () => {
    state.configGlobal = { naming: { prefix: 'trip' } };
    stubConfigApi();

    await switchLanguage('en');

    expect(api).toHaveBeenCalledTimes(1);
    expect(state.configGlobal).toEqual({
      naming: { prefix: 'trip' },
      ui: { language: 'en', desktop_notifications: DEFAULT_DESKTOP_NOTIFICATIONS },
    });
  });

  it('reads the on-disk config instead of unsaved form state', async () => {
    state.configGlobal = { naming: { prefix: 'unsaved' }, ui: { language: 'zh-CN' } };
    state.dirty = true;
    stubConfigApi({ get: { naming: { prefix: 'saved' } } });

    const result = await switchLanguage('en');

    expect(result.persisted).toBe(true);
    expect(putCalls()[0][2].naming).toEqual({ prefix: 'saved' });
    expect(state.configGlobal.naming).toEqual({ prefix: 'unsaved' });
    expect(state.configGlobal.ui.language).toBe('en');
  });

  it('reports a failed write without undoing the live switch', async () => {
    stubConfigApi({ put: { error: 'config_path not available' } });

    const result = await switchLanguage('en');

    expect(result).toEqual({
      ok: true,
      locale: 'en',
      persisted: false,
      error: 'config_path not available',
    });
    expect(getLocale()).toBe('en');
  });

  it('reports a failed read as an unpersisted switch', async () => {
    api.mockRejectedValue(new Error('network down'));

    const result = await switchLanguage('en');

    expect(result).toEqual({ ok: true, locale: 'en', persisted: false, error: 'network down' });
    expect(getLocale()).toBe('en');
  });
});

describe('header language switcher', () => {
  it('labels the button and toggles between the shipped locales', async () => {
    mountHeader();
    stubConfigApi();
    const button = initLanguageSwitcher(document);
    const label = document.getElementById('btn-lang-label');

    expect(button).toBe(document.getElementById('btn-lang'));
    expect(label.textContent).toBe(ZH_LABEL);

    await button.onclick();
    expect(getLocale()).toBe('en');
    expect(label.textContent).toBe('EN');

    await button.onclick();
    expect(getLocale()).toBe('zh-CN');
    expect(label.textContent).toBe(ZH_LABEL);
  });

  it('ignores clicks while a switch is still in flight', async () => {
    mountHeader();
    let resolvePut;
    api.mockImplementation((method) => {
      if (method === 'GET') return Promise.resolve({});
      return new Promise((resolve) => { resolvePut = resolve; });
    });
    const button = initLanguageSwitcher(document);

    const inFlight = button.onclick();
    expect(button.disabled).toBe(true);
    await button.onclick();
    await flush();
    expect(putCalls()).toHaveLength(1);

    resolvePut({ ok: true });
    await inFlight;
    expect(button.disabled).toBe(false);
  });

  it('toasts when the switch could not be persisted', async () => {
    mountHeader();
    stubConfigApi({ put: { error: 'read-only' } });
    const button = initLanguageSwitcher(document);

    await button.onclick();

    expect(addToast).toHaveBeenCalledTimes(1);
    expect(addToast.mock.calls[0][0]).toContain('read-only');
    expect(addToast.mock.calls[0][1]).toBe('warning');
    expect(addToast.mock.calls[0][3]).toEqual({ persist: false });
    expect(getLocale()).toBe('en');
  });

  it('returns null without a header button', () => {
    document.body.innerHTML = '';
    expect(initLanguageSwitcher(document)).toBeNull();
  });
});
