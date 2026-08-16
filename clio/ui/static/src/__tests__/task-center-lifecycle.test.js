import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({ api: vi.fn() }));

import { api } from '../api.js';
import { subscribeTask } from '../task-center.js';

let source;

class FakeEventSource {
  constructor(url) {
    this.url = url;
    source = this;
  }
}

describe('task lifecycle subscription', () => {
  beforeEach(() => {
    globalThis.EventSource = FakeEventSource;
    sessionStorage.clear();
    api.mockReset();
  });

  it('delivers a completed snapshot and deduplicates replayed sequence ids', async () => {
    api.mockResolvedValue({
      task: { id: 'task-1', status: 'succeeded' },
      events: [{ seq: 5, task_id: 'task-1', type: 'status' }],
    });
    const listener = vi.fn();

    const stop = await subscribeTask('task-1', listener);

    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener.mock.calls[0][0].snapshot).toBe(true);
    source.onmessage({
      data: JSON.stringify({
        seq: 5,
        task: { id: 'task-1', status: 'succeeded' },
        event: { seq: 5, task_id: 'task-1' },
      }),
    });
    source.onmessage({
      data: JSON.stringify({
        seq: 6,
        task: { id: 'task-1', status: 'succeeded' },
        event: { seq: 6, task_id: 'task-1' },
      }),
    });
    expect(listener).toHaveBeenCalledTimes(2);
    stop();
  });
});
