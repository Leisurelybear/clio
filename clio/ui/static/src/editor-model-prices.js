/**
 * Official model price presets and exact configured-price helpers.
 *
 * Prices are USD per 1M tokens and are snapshots, not live API lookups.
 */
import { t } from './i18n.js';

export const PRICE_PRESET_VERIFIED_AT = '2026-11-18';

export const OFFICIAL_PRICE_SOURCES = Object.freeze({
  gemini: 'https://ai.google.dev/gemini-api/docs/pricing',
  openai: 'https://platform.openai.com/docs/pricing',
  deepseek: 'https://api-docs.deepseek.com/quick_start/pricing',
});

function price(prompt, completion, extra = {}) {
  return Object.freeze({ prompt, completion, ...extra });
}

export const MODEL_PRICE_CATALOG = Object.freeze({
  gemini: Object.freeze({
    source: OFFICIAL_PRICE_SOURCES.gemini,
    models: Object.freeze({
      'gemini-4-carbon': price(2, 10),
      'gemini-4-argon': price(2, 10),
      'gemini-4-hydrogen': price(1.5, 7.5),
      'gemini-3.8-flash': price(0.75, 3.75),
      'gemini-3.6-flash': price(0.75, 3.75),
      'gemini-3.5-flash-lite': price(0.3, 2.5),
      'gemini-3.1-flash-lite': price(0.25, 1.5),
      'gemini-3.1-pro-preview': price(2, 12),
      'gemini-3-flash-preview': price(0.5, 3),
      'gemini-2.5-pro': price(1.25, 10),
      'gemini-2.5-flash': price(0.3, 2.5),
      'gemini-2.5-flash-lite': price(0.1, 0.4),
    }),
  }),
  openai: Object.freeze({
    source: OFFICIAL_PRICE_SOURCES.openai,
    models: Object.freeze({
      'gpt-6.2-astra': price(8, 40),
      'gpt-6.2-sol': price(2, 10),
      'gpt-6-astra': price(10, 50),
      'gpt-6.1-sol': price(2, 10),
      'gpt-6.1-terra': price(1, 5),
      'gpt-6-luna': price(0.1, 0.5),
      'gpt-6-sol': price(2, 10),
      'gpt-5.6-sol': price(4, 20),
      'gpt-5.6-terra': price(2, 12),
      'gpt-5.6-luna': price(0.2, 1.2),
      'gpt-5.5': price(5, 30),
      'gpt-5.5-pro': price(30, 180),
      'gpt-5.4': price(2.5, 15),
      'gpt-5.4-pro': price(30, 180),
      'gpt-5.4-mini': price(0.75, 4.5),
      'gpt-5.4-nano': price(0.2, 1.25),
      'gpt-5.2': price(1.75, 14),
      'gpt-5.2-pro': price(21, 168),
      'gpt-5.1': price(1.25, 10),
      'gpt-5': price(1.25, 10),
      'gpt-5-mini': price(0.25, 2),
      'gpt-5-nano': price(0.05, 0.4),
      'gpt-5-pro': price(15, 120),
      'gpt-4.1': price(2, 8),
      'gpt-4.1-mini': price(0.4, 1.6),
      'gpt-4o': price(2.5, 10),
      'gpt-4o-mini': price(0.15, 0.6),
      'o3': price(2, 8),
      'o3-pro': price(20, 80),
    }),
  }),
  deepseek: Object.freeze({
    source: OFFICIAL_PRICE_SOURCES.deepseek,
    peakPricing: true,
    models: Object.freeze({
      'deepseek-flash': price(0.3, 1.2, { lowPrompt: 0.15, lowCompletion: 0.6 }),
      'deepseek-v4-pro': price(1.32, 3.96, { lowPrompt: 0.66, lowCompletion: 1.98 }),
    }),
  }),
});

function _providerName(provider) {
  return String(provider?.name || provider?.provider || '').trim().toLowerCase();
}

function _providerBaseUrl(provider) {
  return String(provider?.base_url || '').trim().toLowerCase();
}

/** Return the matching official catalog key, or null for an unknown provider. */
export function officialPriceCatalogKey(provider) {
  const type = String(provider?.type || '').trim().toLowerCase();
  const name = _providerName(provider);
  const baseUrl = _providerBaseUrl(provider);
  if (type === 'ollama') return 'ollama';
  if (type === 'gemini') return 'gemini';
  if (name.includes('deepseek') || baseUrl.includes('deepseek')) return 'deepseek';
  if (name === 'openai' || baseUrl.includes('api.openai.com') || baseUrl.includes('platform.openai.com')) {
    return 'openai';
  }
  return null;
}

/** Exact official preset for a provider/model pair. Unknown models return null. */
export function modelPriceFor(provider, model) {
  const key = officialPriceCatalogKey(provider);
  if (key === 'ollama') return [0, 0];
  const models = key ? MODEL_PRICE_CATALOG[key]?.models : null;
  if (!models || !Object.prototype.hasOwnProperty.call(models, model)) return null;
  const entry = models[model];
  return entry ? [entry.prompt, entry.completion] : null;
}

/** Official source URL advertised in the UI for the recognized catalog. */
export function officialPriceSourceFor(provider) {
  const key = officialPriceCatalogKey(provider);
  return key ? MODEL_PRICE_CATALOG[key]?.source || '' : '';
}

function _strictNonNegativeNumber(value) {
  if (value === null || value === undefined || typeof value === 'boolean') return null;
  if (typeof value === 'string' && value.trim() === '') return null;
  if (typeof value !== 'number' && typeof value !== 'string') return null;
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) return null;
  return number;
}

function _validPricePair(prompt, completion) {
  const p = _strictNonNegativeNumber(prompt);
  const c = _strictNonNegativeNumber(completion);
  return p === null || c === null ? null : [p, c];
}

/** Return whether this provider has an exact price entry for the model. */
export function hasModelPriceEntry(provider, model) {
  const prices = provider?.model_prices;
  return Boolean(
    prices
    && typeof prices === 'object'
    && Object.prototype.hasOwnProperty.call(prices, model),
  );
}

/**
 * Resolve only prices already configured for this provider.
 *
 * Exact model entries win, including a known-free `0 / 0`. Legacy provider
 * prices are a fallback only when both sides are positive. Catalog suggestions
 * are intentionally excluded so task dropdowns never imply an unapplied price.
 */
export function configuredModelPrice(provider, model) {
  const prices = provider?.model_prices;
  if (hasModelPriceEntry(provider, model)) {
    const entry = prices[model];
    if (!entry || typeof entry !== 'object') return null;
    return _validPricePair(entry.prompt_price_per_1m_usd, entry.completion_price_per_1m_usd);
  }
  const prompt = _strictNonNegativeNumber(provider?.prompt_price_per_1m_usd);
  const completion = _strictNonNegativeNumber(provider?.completion_price_per_1m_usd);
  if (prompt !== null && completion !== null && prompt > 0 && completion > 0) {
    return [prompt, completion];
  }
  return null;
}

function _formatUsd(value) {
  return `$${value.toFixed(2)}`;
}

/** Format a known price pair, explicitly distinguishing free from unknown. */
export function formatModelPrice(prompt, completion, options = {}) {
  const pair = _validPricePair(prompt, completion);
  if (!pair) return t('settings.provider.price.unknown');
  const [p, c] = pair;
  if (p === 0 && c === 0) return t('settings.provider.price.free');
  const value = `${_formatUsd(p)} / ${_formatUsd(c)}`;
  return options.unit === false ? value : `${value} / ${t('settings.provider.price.unit_1m')}`;
}

/**
 * Return a copy with official prices added only for models that have no entry.
 * Existing custom prices and malformed entries are never overwritten.
 */
export function applyMissingPricePresets(provider, models) {
  const next = { ...(provider || {}) };
  const existing = Object.create(null);
  if (provider?.model_prices && typeof provider.model_prices === 'object') {
    for (const model of Object.keys(provider.model_prices)) {
      existing[model] = provider.model_prices[model];
    }
  }
  for (const model of Array.isArray(models) ? models : []) {
    if (Object.prototype.hasOwnProperty.call(existing, model)) continue;
    const preset = modelPriceFor(next, model);
    if (!preset) continue;
    existing[model] = {
      prompt_price_per_1m_usd: preset[0],
      completion_price_per_1m_usd: preset[1],
    };
  }
  next.model_prices = existing;
  return next;
}