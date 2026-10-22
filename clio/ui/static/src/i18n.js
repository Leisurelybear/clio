const SUPPORTED_LOCALES = ['zh-CN', 'en'];
const DEFAULT_LOCALE = 'zh-CN';
const FALLBACK_LOCALES = ['en', 'zh-CN'];
const STORAGE_KEY = 'clio.locale';
const LOCALE_CHANGED_EVENT = 'clio:locale-changed';

const _catalogs = new Map();
const _listeners = new Set();
let _locale = DEFAULT_LOCALE;
let _loadPromise = null;

export function normalizeLocale(value, fallback = DEFAULT_LOCALE) {
  const raw = String(value || '').trim().toLowerCase();
  if (!raw) return fallback;
  if (raw === 'zh' || raw === 'zh-cn' || raw.startsWith('zh-')) return 'zh-CN';
  if (raw === 'en' || raw.startsWith('en-')) return 'en';
  return fallback;
}

export function isSupportedLocale(value) {
  return SUPPORTED_LOCALES.includes(String(value || ''));
}

export function getLocale() {
  return _locale;
}

export function readStoredLocale(fallback = DEFAULT_LOCALE) {
  try {
    return normalizeLocale(globalThis.localStorage?.getItem(STORAGE_KEY), fallback);
  } catch {
    return fallback;
  }
}

export function setLocale(value) {
  const next = normalizeLocale(value);
  const changed = next !== _locale;
  _locale = next;
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, _locale);
  } catch { /* storage may be unavailable */ }
  if (globalThis.document?.documentElement) {
    globalThis.document.documentElement.lang = _locale;
  }
  if (changed) _emitLocaleChanged(_locale);
  return _locale;
}

function _emitLocaleChanged(locale) {
  _listeners.forEach(callback => {
    try {
      callback(locale);
    } catch {
      // A broken listener must never abort the language switch.
    }
  });
  const target = globalThis.document;
  if (target?.dispatchEvent && typeof globalThis.CustomEvent === 'function') {
    target.dispatchEvent(new globalThis.CustomEvent(LOCALE_CHANGED_EVENT, { detail: { locale } }));
  }
}

/** Subscribe to locale changes. Returns an unsubscribe function. */
export function onLocaleChange(callback) {
  if (typeof callback !== 'function') return () => {};
  _listeners.add(callback);
  return () => _listeners.delete(callback);
}

/** Switch locale and re-apply static `data-i18n` markup in one call. */
export function applyLocale(value) {
  setLocale(value);
  applyTranslations();
  return _locale;
}

export function setCatalog(locale, entries) {
  const normalized = normalizeLocale(locale);
  _catalogs.set(normalized, entries && typeof entries === 'object' ? entries : {});
}

function _lookup(catalog, key) {
  if (!catalog || typeof catalog !== 'object') return undefined;
  if (Object.prototype.hasOwnProperty.call(catalog, key)) return catalog[key];
  let current = catalog;
  for (const part of String(key).split('.')) {
    if (!current || typeof current !== 'object' || !Object.prototype.hasOwnProperty.call(current, part)) {
      return undefined;
    }
    current = current[part];
  }
  return current;
}

function _interpolate(template, params) {
  if (!params || typeof params !== 'object') return String(template);
  return String(template).replace(/\{([A-Za-z0-9_]+)\}/g, (match, name) => {
    const value = params[name];
    return value === undefined || value === null ? match : String(value);
  });
}

export function t(key, params = {}, locale = _locale) {
  const candidates = [normalizeLocale(locale), ...FALLBACK_LOCALES];
  const seen = new Set();
  for (const candidate of candidates) {
    if (seen.has(candidate)) continue;
    seen.add(candidate);
    const value = _lookup(_catalogs.get(candidate), key);
    if (typeof value === 'string') return _interpolate(value, params);
  }
  return String(key);
}

/** True when any catalog in the fallback chain defines `key`. */
export function hasKey(key, locale = _locale) {
  const candidates = [normalizeLocale(locale), ...FALLBACK_LOCALES];
  const seen = new Set();
  for (const candidate of candidates) {
    if (seen.has(candidate)) continue;
    seen.add(candidate);
    if (typeof _lookup(_catalogs.get(candidate), key) === 'string') return true;
  }
  return false;
}

function _parseCatalog(payload, locale) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new Error(`invalid i18n catalog: ${locale}`);
  }
  return payload;
}

export async function loadCatalogs({ fetchImpl = globalThis.fetch, baseUrl = '/static/locales' } = {}) {
  if (typeof fetchImpl !== 'function') return 0;
  let loaded = 0;
  await Promise.all(SUPPORTED_LOCALES.map(async locale => {
    try {
      const response = await fetchImpl(`${baseUrl}/${encodeURIComponent(locale)}.json`);
      if (!response?.ok) return;
      setCatalog(locale, _parseCatalog(await response.json(), locale));
      loaded += 1;
    } catch {
      // Missing or broken catalogs fall back to the key string, never block boot.
    }
  }));
  return loaded;
}

export async function initI18n(locale, options = {}) {
  if (!_loadPromise) _loadPromise = loadCatalogs(options);
  await _loadPromise;
  setLocale(locale);
  applyTranslations(options.root || globalThis.document);
  return _locale;
}

export function applyTranslations(root = globalThis.document) {
  if (!root?.querySelectorAll) return 0;
  let count = 0;
  const apply = (selector, attribute) => {
    const dataAttribute = selector.slice(1, -1);
    root.querySelectorAll(selector).forEach(element => {
      const key = element.getAttribute(dataAttribute);
      if (!key) return;
      const value = t(key);
      if (attribute) element.setAttribute(attribute, value);
      else element.textContent = value;
      count += 1;
    });
  };
  apply('[data-i18n]', null);
  apply('[data-i18n-title]', 'title');
  apply('[data-i18n-aria-label]', 'aria-label');
  apply('[data-i18n-placeholder]', 'placeholder');
  if (globalThis.document?.documentElement) {
    globalThis.document.documentElement.lang = _locale;
  }
  return count;
}

export function _resetI18nForTests() {
  _catalogs.clear();
  _listeners.clear();
  _locale = DEFAULT_LOCALE;
  _loadPromise = null;
}

export { DEFAULT_LOCALE, LOCALE_CHANGED_EVENT, STORAGE_KEY, SUPPORTED_LOCALES };
