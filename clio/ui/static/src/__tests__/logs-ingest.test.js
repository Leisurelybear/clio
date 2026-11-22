import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', async () => {
  const actual = await vi.importActual('../api.js');
  return { ...actual, api: vi.fn() };
});

import { api } from '../api.js';
import { renderLogs, stopLogsPolling } from '../editor-config.js';
import { LOGS_BUFFER_MAX } from '../logs-buffer.js';

describe('live log ingest', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    document.body.innerHTML = '<div id="tab-logs"></div>';
    api.mockReset();
  });

  afterEach(() => {
    stopLogsPolling();
    vi.useRealTimers();
  });

  it('trims the oldest rows on ring overflow without a full repaint', async () => {
    const first = Array.from({ length: LOGS_BUFFER_MAX - 1 }, (_, i) => ({ ts: 1, text: `line ${i}` }));
    const second = [{ ts: 1, text: 'tail a' }, { ts: 1, text: 'tail b' }, { ts: 1, text: 'tail c' }];
    let polls = 0;
    api.mockImplementation(async (method, url) => {
      if (url === '/api/logs/files') return { files: [] };
      if (url.startsWith('/api/logs?')) {
        polls += 1;
        if (polls === 1) return { logs: first, total: first.length };
        if (polls === 2) return { logs: second, total: first.length + second.length };
        return { logs: [], total: first.length + second.length };
      }
      return {};
    });

    renderLogs();
    await vi.advanceTimersByTimeAsync(0);
    const view = document.getElementById('logs-view');
    expect(view.children).toHaveLength(LOGS_BUFFER_MAX - 1);
    const survivor = view.children[2];

    await vi.advanceTimersByTimeAsync(2000);
    expect(view.children).toHaveLength(LOGS_BUFFER_MAX);
    // Same node object: rows were trimmed, not rebuilt.
    expect(view.children[0]).toBe(survivor);
    expect(view.lastElementChild.textContent).toContain('tail c');
    expect(document.getElementById('logs-match-count').textContent).toContain(String(LOGS_BUFFER_MAX));
  });
});
