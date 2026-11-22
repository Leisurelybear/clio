import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({ api: vi.fn(), icon: vi.fn(() => '') }));
vi.mock('../task-center.js', () => ({ fetchTask: vi.fn(), subscribeTaskEvents: vi.fn(() => () => {}) }));
vi.mock('../confirm.js', () => ({ confirmDialog: vi.fn() }));

import { api } from '../api.js';
import { _startModelDlPoll } from '../editor-models.js';

const statusCalls = () => api.mock.calls.filter(([, url]) => url === '/api/whisper/install/status').length;

describe('legacy whisper download polling', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    api.mockReset();
    document.body.innerHTML = '<div id="whisper-model-mgmt"><div id="model-dl-bar"></div><div id="model-dl-msg"></div></div>';
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('never overlaps requests when the server is slower than the interval', async () => {
    let finish;
    api.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    _startModelDlPoll();
    await vi.advanceTimersByTimeAsync(5_000);
    expect(statusCalls()).toBe(1);

    finish({ running: true, status: 'downloading', progress_pct: 10 });
    await vi.advanceTimersByTimeAsync(500);
    expect(statusCalls()).toBe(1);
    await vi.advanceTimersByTimeAsync(600);
    expect(statusCalls()).toBe(2);
  });

  it('stops polling once the settings panel is gone', async () => {
    api.mockResolvedValue({ running: true, status: 'downloading', progress_pct: 10 });
    _startModelDlPoll();
    await vi.advanceTimersByTimeAsync(1_000);
    const before = statusCalls();
    document.body.innerHTML = '';
    await vi.advanceTimersByTimeAsync(10_000);
    expect(statusCalls()).toBe(before);
  });
});
