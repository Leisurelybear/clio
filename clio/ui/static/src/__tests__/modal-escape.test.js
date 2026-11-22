import { describe, expect, it, vi } from 'vitest';
import { closeTopModal } from '../modal-escape.js';

describe('closeTopModal', () => {
  it('returns false when no modal is visible', () => {
    document.body.innerHTML = '<div class="modal" style="display:none"></div>';
    expect(closeTopModal()).toBe(false);
  });

  it('runs the registered close routine of the topmost modal', () => {
    document.body.innerHTML = '<div class="modal" id="below"></div>';
    const top = document.createElement('div');
    top.className = 'modal';
    top.innerHTML = '<input type="password" value="sk-secret">';
    const close = vi.fn(() => { top.querySelector('input').value = ''; top.remove(); });
    top._closeModal = close;
    document.body.appendChild(top);

    expect(closeTopModal()).toBe(true);
    expect(close).toHaveBeenCalledOnce();
    expect(top.isConnected).toBe(false);
    expect(document.getElementById('below').style.display).toBe('');
  });

  it('falls back to hiding static modals', () => {
    document.body.innerHTML = '<div class="modal" id="m" style="display:flex"></div>';
    expect(closeTopModal()).toBe(true);
    expect(document.getElementById('m').style.display).toBe('none');
  });
});
