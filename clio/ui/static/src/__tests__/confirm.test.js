import { beforeEach, describe, expect, it } from 'vitest';

import { alertDialog, confirmDialog, promptDialog } from '../confirm.js';

function currentDialog() {
  return document.querySelector('.clio-dialog');
}

function click(selector) {
  const button = currentDialog().querySelector(selector);
  button.click();
}

beforeEach(() => {
  document.body.innerHTML = '';
});

describe('confirmDialog', () => {
  it('renders the message and resolves true on confirm', async () => {
    const pending = confirmDialog('Delete this?');
    expect(currentDialog().textContent).toContain('Delete this?');
    click('[data-dialog-confirm]');
    await expect(pending).resolves.toBe(true);
    expect(currentDialog()).toBeNull();
  });

  it('resolves false on cancel', async () => {
    const pending = confirmDialog('Keep going?');
    click('[data-dialog-cancel]');
    await expect(pending).resolves.toBe(false);
  });

  it('resolves false on Escape and backdrop click', async () => {
    const first = confirmDialog('One');
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await expect(first).resolves.toBe(false);

    const second = confirmDialog('Two');
    currentDialog().querySelector('.modal-backdrop').click();
    await expect(second).resolves.toBe(false);
  });

  it('uses custom labels and exposes an accessible title', async () => {
    const pending = confirmDialog('Overwrite?', { title: 'Careful', confirmText: 'Overwrite', cancelText: 'Keep' });
    const dialog = currentDialog();
    expect(dialog.querySelector('h3').textContent).toBe('Careful');
    expect(dialog.querySelector('[data-dialog-confirm]').textContent).toBe('Overwrite');
    expect(dialog.querySelector('[data-dialog-cancel]').textContent).toBe('Keep');
    expect(dialog.getAttribute('aria-labelledby')).toBe(dialog.querySelector('h3').id);
    click('[data-dialog-cancel]');
    await pending;
  });

  it('keeps message text escaped and preserves line breaks', async () => {
    const pending = confirmDialog('line one\n<b>line two</b>');
    const body = currentDialog().querySelector('.clio-dialog-message');
    expect(body.textContent).toBe('line one\n<b>line two</b>');
    expect(body.querySelector('b')).toBeNull();
    expect(body.style.whiteSpace).toBe('pre-wrap');
    click('[data-dialog-confirm]');
    await pending;
  });

  it('marks destructive actions and focuses cancel first', async () => {
    const pending = confirmDialog('Remove?', { danger: true });
    const dialog = currentDialog();
    expect(dialog.querySelector('[data-dialog-confirm]').classList.contains('btn-danger')).toBe(true);
    expect(document.activeElement).toBe(dialog.querySelector('[data-dialog-cancel]'));
    click('[data-dialog-cancel]');
    await pending;
  });

  it('restores focus to the previously active element', async () => {
    const trigger = document.createElement('button');
    document.body.appendChild(trigger);
    trigger.focus();
    const pending = confirmDialog('Continue?');
    click('[data-dialog-confirm]');
    await pending;
    expect(document.activeElement).toBe(trigger);
  });
});

describe('alertDialog', () => {
  it('shows a single dismiss button and resolves on confirm', async () => {
    const pending = alertDialog('Saved.');
    const dialog = currentDialog();
    expect(dialog.querySelector('[data-dialog-cancel]')).toBeNull();
    expect(dialog.textContent).toContain('Saved.');
    click('[data-dialog-confirm]');
    await expect(pending).resolves.toBeUndefined();
  });

  it('dismisses on Escape', async () => {
    const pending = alertDialog('Heads up.');
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await expect(pending).resolves.toBeUndefined();
  });
});

describe('promptDialog', () => {
  it('returns the edited value', async () => {
    const pending = promptDialog('Name?', { value: 'old' });
    const input = currentDialog().querySelector('input');
    expect(input.value).toBe('old');
    input.value = 'new name';
    click('[data-dialog-confirm]');
    await expect(pending).resolves.toBe('new name');
  });

  it('submits on Enter and cancels to null', async () => {
    const pending = promptDialog('Name?');
    const input = currentDialog().querySelector('input');
    input.value = 'typed';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await expect(pending).resolves.toBe('typed');

    const cancelled = promptDialog('Again?', { value: 'keep' });
    click('[data-dialog-cancel]');
    await expect(cancelled).resolves.toBeNull();
  });

  it('focuses and selects the input', async () => {
    const pending = promptDialog('Name?', { value: 'abc' });
    const input = currentDialog().querySelector('input');
    expect(document.activeElement).toBe(input);
    expect(input.selectionStart).toBe(0);
    expect(input.selectionEnd).toBe(3);
    click('[data-dialog-cancel]');
    await pending;
  });
});
