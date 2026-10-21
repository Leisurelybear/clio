import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', async () => {
  const actual = await vi.importActual('../api.js');
  return {
    ...actual,
    api: vi.fn(),
  };
});

import { api } from '../api.js';
import { renderLogs, stopLogsPolling } from '../editor-config.js';

describe('renderLogs historical source', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="tab-logs"></div>';
    api.mockReset();
  });

  afterEach(() => {
    stopLogsPolling();
  });

  it('opens a disk file read-only and returns to the live tail', async () => {
    api.mockImplementation((method, url) => {
      if (url === '/api/logs/files') {
        return Promise.resolve({
          files: [
            { name: '2026-07-20-21.log', size: 1024, mtime: 1 },
            { name: '2026-07-20-22.log', size: 2048, mtime: 2 },
          ],
        });
      }
      if (url.startsWith('/api/logs/file?')) {
        return Promise.resolve({
          name: '2026-07-20-22.log',
          logs: [{ ts: 1, text: '[ERROR] boom' }],
          truncated: true,
        });
      }
      return Promise.resolve({ logs: [], total: 0 });
    });

    renderLogs();

    await vi.waitFor(() => {
      expect(document.querySelectorAll('#logs-source-select option')).toHaveLength(3);
    });
    const select = document.querySelector('#logs-source-select');
    const status = document.querySelector('#logs-source-status');
    const clearBtn = document.querySelector('#btn-logs-clear');
    expect(select.textContent).toContain('2026-07-20 22:00');

    select.value = '2026-07-20-22.log';
    select.dispatchEvent(new Event('change'));

    await vi.waitFor(() => {
      expect(document.querySelector('#logs-view').textContent).toContain('boom');
    });
    expect(api).toHaveBeenCalledWith('GET', '/api/logs/file?name=2026-07-20-22.log');
    expect(clearBtn.disabled).toBe(true);
    expect(status.textContent).toContain('1 行');
    expect(status.textContent).toContain('仅显示文件末尾');

    select.value = '';
    select.dispatchEvent(new Event('change'));

    expect(clearBtn.disabled).toBe(false);
    expect(status.textContent).toBe('实时');
  });
});
