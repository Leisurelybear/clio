import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', async () => {
  const actual = await vi.importActual('../api.js');
  return {
    ...actual,
    api: vi.fn(),
  };
});

import { api } from '../api.js';
import { applyLocale } from '../i18n.js';
import { state } from '../state.js';
import { renderLogs, stopLogsPolling } from '../editor-config.js';

describe('logs pane copy follows the active locale', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="tab-logs"></div>';
    api.mockReset();
    api.mockImplementation(() => Promise.resolve({ logs: [], total: 0 }));
    state.currentEntity = 'logs';
  });

  afterEach(() => {
    state.currentEntity = null;
    stopLogsPolling();
    applyLocale('zh-CN');
    document.body.innerHTML = '';
  });

  it('re-renders the toolbar copy when the locale changes', () => {
    applyLocale('zh-CN');
    renderLogs();
    expect(document.querySelector('.logs-toolbar-title').textContent).toBe('\u4f1a\u8bdd\u65e5\u5fd7');
    expect(document.getElementById('btn-logs-clear').textContent).toBe('\u6e05\u7a7a');

    applyLocale('en');

    expect(document.querySelector('.logs-toolbar-title').textContent).toBe('Session log');
    expect(document.getElementById('btn-logs-clear').textContent).toBe('Clear');
  });
});
