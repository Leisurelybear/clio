import { beforeEach, describe, it, expect, vi } from 'vitest';

describe('addToast a11y and duration', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="toast-container" class="toast-container"></div>';
    vi.resetModules();
    vi.useFakeTimers();
  });

  it('sets role=status and aria-live on the toast container once', async () => {
    const { addToast } = await import('../toast.js');
    addToast('hello', 'info', 1000);
    const container = document.getElementById('toast-container');
    expect(container.getAttribute('role')).toBe('status');
    expect(container.getAttribute('aria-live')).toBe('polite');
    expect(container.getAttribute('aria-atomic')).toBe('true');
  });

  it('uses longer default duration for error toasts', async () => {
    const { addToast, DEFAULT_DURATIONS } = await import('../toast.js');
    expect(DEFAULT_DURATIONS.error).toBeGreaterThanOrEqual(8000);
    expect(DEFAULT_DURATIONS.info).toBeLessThanOrEqual(5000);

    addToast('boom', 'error'); // no duration → use default
    const toast = document.querySelector('.toast.error');
    expect(toast).toBeTruthy();
    // still visible before default error duration
    vi.advanceTimersByTime(DEFAULT_DURATIONS.error - 100);
    expect(document.querySelector('.toast.error')).toBeTruthy();
  });

  it('honors explicit duration overrides', async () => {
    const { addToast } = await import('../toast.js');
    addToast('short', 'error', 500);
    vi.advanceTimersByTime(600);
    // removing starts animation; toast may still be in DOM with .removing
    const toast = document.querySelector('.toast');
    expect(toast?.classList.contains('removing') || !toast).toBe(true);
  });

  it('duration 0 stays sticky even when dequeued', async () => {
    const { addToast } = await import('../toast.js');
    // Fill visible slots so the sticky toast waits in queue.
    addToast('a', 'info', 100);
    addToast('b', 'info', 100);
    addToast('c', 'info', 100);
    addToast('sticky', 'info', 0);
    // Expire the first toast and flush its remove animation.
    vi.advanceTimersByTime(150);
    const removing = document.querySelector('.toast.removing');
    removing?.dispatchEvent(new Event('animationend'));
    const sticky = [...document.querySelectorAll('.toast')].find((t) =>
      t.textContent?.includes('sticky'),
    );
    expect(sticky).toBeTruthy();
    vi.advanceTimersByTime(10_000);
    expect([...document.querySelectorAll('.toast')].some((t) =>
      t.textContent?.includes('sticky'),
    )).toBe(true);
  });
});
