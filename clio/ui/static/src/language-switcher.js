/** Header language switcher plus the `ui` config-block normalizer.
 *
 * `ui.language` is the persisted source of truth, but configs written before
 * that field existed have no `ui:` block at all, so the settings card never
 * rendered. `ensureGlobalUiDefaults()` fills the block in on load; the header
 * button gives the same setting a one-click entry point.
 */
import { api } from './api.js';
import { applyLocale, getLocale, normalizeLocale, onLocaleChange, SUPPORTED_LOCALES, t } from './i18n.js';
import { state } from './state.js';
import { addToast } from './toast.js';

// Re-exported: `i18n.js` owns the one definition of the shipped locale list.
export { SUPPORTED_LOCALES };
export const DEFAULT_DESKTOP_NOTIFICATIONS = false;

const BUTTON_LABELS = { 'zh-CN': '\u4e2d', en: 'EN' };

/** Short label for the header button: the locale the user is currently on. */
export function languageButtonLabel(locale = getLocale()) {
  return BUTTON_LABELS[normalizeLocale(locale)] || 'EN';
}

/** The locale a click on the header button switches to. */
export function nextLocale(locale = getLocale()) {
  const index = SUPPORTED_LOCALES.indexOf(normalizeLocale(locale));
  return SUPPORTED_LOCALES[(index + 1) % SUPPORTED_LOCALES.length];
}

/**
 * Guarantee a complete `ui` block so the settings card renders and saves.
 * Mutates and returns the given object; a non-object is returned untouched.
 */
export function ensureGlobalUiDefaults(global, locale = getLocale()) {
  if (!global || typeof global !== 'object') return global;
  const ui = global.ui && typeof global.ui === 'object' ? { ...global.ui } : {};
  if (!ui.language) ui.language = normalizeLocale(locale);
  if (typeof ui.desktop_notifications !== 'boolean') {
    ui.desktop_notifications = DEFAULT_DESKTOP_NOTIFICATIONS;
  }
  global.ui = ui;
  return global;
}

async function _loadGlobalConfig() {
  const result = await api('GET', '/api/config/global');
  if (result?.error) throw new Error(result.error);
  return result && typeof result === 'object' ? result : {};
}

/**
 * Switch the UI language immediately, then persist it into config.yaml.
 * Unsaved settings edits are never sent: while the config form is dirty the
 * on-disk global config is read instead, and only `ui.language` is copied back
 * into the form state so a later save cannot revert the switch.
 * Returns `{ ok, locale, persisted }`; `persisted: false` means the switch is
 * live but the write failed (for example when no config file exists yet).
 */
export async function switchLanguage(locale = nextLocale(), options = {}) {
  const next = normalizeLocale(locale);
  // Update the form state first: applying the locale re-renders the settings
  // card, and that render must already see the new ui.language value.
  if (state.configGlobal && typeof state.configGlobal === 'object') {
    ensureGlobalUiDefaults(state.configGlobal, next).ui.language = next;
  }
  applyLocale(next);
  try {
    const reused = options.global || (!state.dirty ? state.configGlobal : null);
    const source = reused && typeof reused === 'object' ? reused : await _loadGlobalConfig();
    const payload = ensureGlobalUiDefaults(source, next);
    payload.ui.language = next;
    const result = await api('PUT', '/api/config/global', payload);
    if (result?.error) throw new Error(result.error);
    return { ok: true, locale: next, persisted: true };
  } catch (e) {
    return { ok: true, locale: next, persisted: false, error: e.message };
  }
}

/** Wire the header button. Returns the button, or null when it is absent. */
export function initLanguageSwitcher(root = globalThis.document) {
  const button = root?.getElementById?.('btn-lang');
  if (!button) return null;
  const label = root.getElementById('btn-lang-label') || button;
  const sync = () => {
    label.textContent = languageButtonLabel();
  };
  sync();
  onLocaleChange(sync);
  button.onclick = async () => {
    if (button.disabled) return;
    button.disabled = true;
    try {
      const result = await switchLanguage();
      if (!result.persisted) {
        addToast(t('shell.language_save_failed', { detail: result.error || '' }), 'warning', undefined, {
          persist: false,
        });
      }
    } finally {
      button.disabled = false;
    }
  };
  return button;
}
