import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({
  api: vi.fn(() => Promise.resolve({})),
  icon: vi.fn(() => ''),
}));

import { applyLocale } from '../i18n.js';
import { renderTokens } from '../editor-config.js';

const USAGE = {
  total: { total_tokens: 10, prompt_tokens: 6, completion_tokens: 4 },
  by_model: { 'gemini-2.5-flash': { calls: 1, prompt_tokens: 6, completion_tokens: 4, total_tokens: 10 } },
  by_task: { video_analyze: { calls: 1, prompt_tokens: 6, completion_tokens: 4, total_tokens: 10 } },
  history: [],
};

describe('token stats localization', () => {
  afterEach(() => applyLocale('zh-CN'));

  it('renders the token tables in the active locale', async () => {
    const { api } = await import('../api.js');
    api.mockImplementation((method, url) => {
      if (url === '/api/token-usage') return Promise.resolve(USAGE);
      return Promise.resolve({});
    });
    document.body.innerHTML = '<div id="tab-tokens"></div>';

    applyLocale('en');
    await renderTokens();
    const enHtml = document.getElementById('tab-tokens').innerHTML;
    expect(enHtml).toContain('Total tokens');
    expect(enHtml).toContain('By model');
    expect(enHtml).toContain('By task');

    applyLocale('zh-CN');
    await renderTokens();
    const zhHtml = document.getElementById('tab-tokens').innerHTML;
    expect(zhHtml).toContain('\u603b Token');
    expect(zhHtml).toContain('\u6309\u6a21\u578b');
  });

  it('localizes the empty state', async () => {
    const { api } = await import('../api.js');
    api.mockImplementation(() => Promise.resolve({}));
    document.body.innerHTML = '<div id="tab-tokens"></div>';

    applyLocale('en');
    await renderTokens();
    expect(document.getElementById('tab-tokens').innerHTML).toContain('No token usage data yet');

    applyLocale('zh-CN');
    await renderTokens();
    expect(document.getElementById('tab-tokens').innerHTML).toContain('\u6682\u65e0 token \u4f7f\u7528\u6570\u636e');
  });
});
