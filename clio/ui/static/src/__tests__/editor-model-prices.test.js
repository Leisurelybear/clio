import { describe, expect, it } from 'vitest';
import {
  applyMissingPricePresets,
  configuredModelPrice,
  formatModelPrice,
  modelPriceFor,
  officialPriceCatalogKey,
} from '../editor-model-prices.js';

describe('official model price presets', () => {
  it('matches exact model ids in the recognized catalogs', () => {
    expect(modelPriceFor({ type: 'gemini', name: 'gemini' }, 'gemini-3.1-flash-lite')).toEqual([0.25, 1.5]);
    expect(modelPriceFor({ type: 'openai', name: 'openai' }, 'gpt-5.4-mini')).toEqual([0.75, 4.5]);
    expect(modelPriceFor({ type: 'openai', name: 'deepseek', base_url: 'https://api.deepseek.com/v1' }, 'deepseek-flash')).toEqual([0.3, 1.2]);
  });

  it('does not inherit [OI] prices for compatible providers', () => {
    expect(officialPriceCatalogKey({ type: 'openai', name: 'qwen', base_url: 'https://dashscope.example/v1' })).toBeNull();
    expect(officialPriceCatalogKey({ type: 'openai', name: 'kimi', base_url: 'https://api.moonshot.example/v1' })).toBeNull();
    expect(modelPriceFor({ type: 'openai', name: 'qwen' }, 'gpt-5.4-mini')).toBeNull();
  });

  it('recognizes only official [OI] names or hosts', () => {
    expect(officialPriceCatalogKey({ type: 'openai', name: 'openai' })).toBe('openai');
    expect(officialPriceCatalogKey({ type: 'openai', base_url: 'https://api.openai.com/v1' })).toBe('openai');
    expect(officialPriceCatalogKey({ type: 'openai', name: 'openai-compatible' })).toBeNull();
  });

  it('treats local Ollama models as known free', () => {
    expect(modelPriceFor({ type: 'ollama', name: 'local', models: ['anything'] }, 'anything')).toEqual([0, 0]);
  });

  it('adds only missing prices and never mutates the caller', () => {
    const provider = {
      type: 'gemini',
      name: 'gemini',
      models: ['gemini-3.1-flash-lite', 'gemini-2.5-flash', 'custom-model'],
      model_prices: {
        'gemini-2.5-flash': { prompt_price_per_1m_usd: 9, completion_price_per_1m_usd: 10 },
      },
    };
    const next = applyMissingPricePresets(provider, provider.models);

    expect(next).not.toBe(provider);
    expect(next.model_prices['gemini-3.1-flash-lite']).toEqual({
      prompt_price_per_1m_usd: 0.25,
      completion_price_per_1m_usd: 1.5,
    });
    expect(next.model_prices['gemini-2.5-flash']).toEqual({
      prompt_price_per_1m_usd: 9,
      completion_price_per_1m_usd: 10,
    });
    expect(next.model_prices['custom-model']).toBeUndefined();
    expect(provider.model_prices['gemini-3.1-flash-lite']).toBeUndefined();
  });

  it('does not fill prices for models that are not registered', () => {
    const next = applyMissingPricePresets({ type: 'gemini', name: 'gemini' }, ['gemini-2.5-flash']);
    expect(Object.keys(next.model_prices)).toEqual(['gemini-2.5-flash']);
  });

  it('keeps DeepSeek peak and off-peak metadata in the catalog', async () => {
    const { MODEL_PRICE_CATALOG } = await import('../editor-model-prices.js');
    expect(MODEL_PRICE_CATALOG.deepseek.peakPricing).toBe(true);
    expect(MODEL_PRICE_CATALOG.deepseek.models['deepseek-v4-pro']).toMatchObject({
      prompt: 1.32,
      completion: 3.96,
      lowPrompt: 0.66,
      lowCompletion: 1.98,
    });
  });
});

describe('configured model prices', () => {
  it('prefers an exact model entry, including free or one-sided zero prices', () => {
    const provider = {
      model_prices: { free: { prompt_price_per_1m_usd: 0, completion_price_per_1m_usd: 0 } },
      prompt_price_per_1m_usd: 2,
      completion_price_per_1m_usd: 10,
    };
    expect(configuredModelPrice(provider, 'free')).toEqual([0, 0]);
    expect(configuredModelPrice(provider, 'other')).toEqual([2, 10]);
  });

  it('allows a single known-free side', () => {
    const provider = {
      model_prices: { model: { prompt_price_per_1m_usd: 0, completion_price_per_1m_usd: 3 } },
    };
    expect(configuredModelPrice(provider, 'model')).toEqual([0, 3]);
  });

  it('does not use catalog suggestions as configured prices', () => {
    const provider = { type: 'gemini', name: 'gemini' };
    expect(configuredModelPrice(provider, 'gemini-2.5-flash')).toBeNull();
  });

  it('rejects invalid exact entries instead of falling back', () => {
    const provider = {
      model_prices: { model: { prompt_price_per_1m_usd: -1, completion_price_per_1m_usd: 3 } },
      prompt_price_per_1m_usd: 2,
      completion_price_per_1m_usd: 10,
    };
    expect(configuredModelPrice(provider, 'model')).toBeNull();
  });
});

describe('model price formatting', () => {
  it('differentiates free, known, and unknown prices', () => {
    expect(formatModelPrice(0, 0)).toBe('免费');
    expect(formatModelPrice(0.25, 1.5)).toBe('$0.25 / $1.50 / 1M');
    expect(formatModelPrice(null, 1)).toBe('未知');
    expect(formatModelPrice(0.25, 1.5, { unit: false })).toBe('$0.25 / $1.50');
  });
});