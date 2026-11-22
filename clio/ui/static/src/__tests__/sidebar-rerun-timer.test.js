import { beforeEach, afterEach, describe, it, expect, vi } from 'vitest';

const handlers = [];
const unsubscribes = [];
vi.mock('../task-center.js', () => ({
  subscribeTaskEvents: vi.fn((fn) => {
    handlers.push(fn);
    const unsub = vi.fn();
    unsubscribes.push(unsub);
    return unsub;
  }),
  fetchTask: vi.fn(() => new Promise(() => {})),
}));
vi.mock('../api.js', () => ({ api: vi.fn(async () => ({})), apiWithMeta: vi.fn(async () => ({ data: {} })), icon: vi.fn(() => '') }));
vi.mock('../toast.js', () => ({ addToast: vi.fn() }));
vi.mock('../sidebar-data.js', () => ({ loadVideos: vi.fn(async () => {}), renderVideoList: vi.fn() }));
vi.mock('../utils.js', async () => {
  const actual = await vi.importActual('../utils.js');
  return { ...actual, setStatus: vi.fn(), updateEntityUI: vi.fn() };
});

import { showRerunProgress } from '../sidebar-rerun.js';
import { loadVideos } from '../sidebar-data.js';

describe('rerun overlay auto-hide', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    handlers.length = 0;
    unsubscribes.length = 0;
    document.body.innerHTML = `
      <div id="rerun-overlay" style="display:none">
        <div class="rerun-title"></div><div class="rerun-file"></div>
        <div class="rerun-status"></div><div class="rerun-progress-fill"></div>
        <div class="rerun-logs"></div>
      </div>`;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('does not hide a new rerun started before the old hide timer fires', () => {
    showRerunProgress('analyze', 'a.mp4', 't1');
    handlers[0]({ task: { id: 't1', kind: 'rerun', status: 'succeeded' } });

    showRerunProgress('analyze', 'b.mp4', 't2');
    vi.advanceTimersByTime(10_000);

    const overlay = document.getElementById('rerun-overlay');
    expect(overlay.style.display).toBe('block');
    expect(overlay.classList.contains('active')).toBe(true);
    expect(unsubscribes[1]).not.toHaveBeenCalled();
    // The finished rerun still refreshes its artifacts.
    expect(loadVideos).toHaveBeenCalled();
  });
});
