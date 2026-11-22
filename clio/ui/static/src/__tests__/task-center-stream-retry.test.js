import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({ api: vi.fn() }));

import { api } from '../api.js';
import { subscribeTaskEvents } from '../task-center.js';

const sources = [];

class FakeEventSource {
  static CLOSED = 2;
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    sources.push(this);
  }
  close() { this.readyState = 2; }
}

describe('task stream recovery', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    globalThis.EventSource = FakeEventSource;
    sessionStorage.clear();
    api.mockReset();
    api.mockResolvedValue({ tasks: [], latest_seq: 0 });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('rebuilds a CLOSED stream from the last cursor and resyncs tasks', async () => {
    const seen = [];
    const stop = subscribeTaskEvents(payload => seen.push(payload));
    expect(sources).toHaveLength(1);
    sources[0].onmessage({ data: JSON.stringify({ seq: 7, task: { id: 't1', status: 'running' } }) });

    // Transient error while the browser is reconnecting: leave it alone.
    sources[0].readyState = 0;
    sources[0].onerror();
    vi.advanceTimersByTime(60_000);
    expect(sources).toHaveLength(1);

    // Browser gave up (e.g. 401 on reconnect).
    sources[0].readyState = 2;
    sources[0].onerror();
    vi.advanceTimersByTime(1_000);
    expect(sources).toHaveLength(2);
    expect(sources[1].url).toContain('after=7');
    expect(api).toHaveBeenCalledWith('GET', '/api/tasks?visibility=all&limit=200');

    sources[1].onmessage({ data: JSON.stringify({ seq: 8, task: { id: 't1', status: 'succeeded' } }) });
    expect(seen.map(p => p.seq)).toEqual([7, 8]);
    stop();
  });
});
