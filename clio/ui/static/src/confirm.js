/**
 * Promise-based replacements for the browser's native dialogs.
 *
 * Native confirm/alert/prompt block the event loop, cannot be styled, and are
 * suppressed by some webviews. Every user-facing question must go through this
 * module instead.
 */
import { t } from './i18n.js';

let _dialogSeq = 0;

function _button(label, className, attribute) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = className;
  button.textContent = label;
  button.setAttribute(attribute, '');
  return button;
}

/**
 * Open a modal dialog and resolve when the user makes a choice.
 * @returns {Promise<boolean|string|null>} kind-specific result.
 */
function _openDialog({ kind, message, title, value = '', placeholder = '', confirmText, cancelText, danger = false }) {
  return new Promise(resolve => {
    const id = `clio-dialog-${++_dialogSeq}`;
    const previousFocus = document.activeElement;
    const modal = document.createElement('div');
    modal.className = 'modal clio-dialog';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');

    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';

    const dialog = document.createElement('div');
    dialog.className = 'modal-dialog';
    dialog.setAttribute('role', 'document');

    const heading = document.createElement('h3');
    heading.id = `${id}-title`;
    heading.textContent = title;
    modal.setAttribute('aria-labelledby', heading.id);

    const body = document.createElement('p');
    body.className = 'clio-dialog-message';
    body.style.whiteSpace = 'pre-wrap';
    body.textContent = message;

    const actions = document.createElement('div');
    actions.className = 'modal-actions';
    const cancelButton = kind === 'alert' ? null : _button(cancelText, 'btn-secondary', 'data-dialog-cancel');
    const confirmButton = _button(confirmText, danger ? 'btn-danger' : 'btn-primary', 'data-dialog-confirm');

    let input = null;
    if (kind === 'prompt') {
      input = document.createElement('input');
      input.type = 'text';
      input.className = 'form-input';
      input.value = value;
      if (placeholder) input.placeholder = placeholder;
    }

    if (cancelButton) actions.appendChild(cancelButton);
    actions.appendChild(confirmButton);
    dialog.append(heading, body);
    if (input) dialog.appendChild(input);
    dialog.appendChild(actions);
    modal.append(backdrop, dialog);
    document.body.appendChild(modal);

    let settled = false;
    const finish = result => {
      if (settled) return;
      settled = true;
      document.removeEventListener('keydown', onKeyDown, true);
      modal.remove();
      if (previousFocus && typeof previousFocus.focus === 'function' && document.contains(previousFocus)) {
        previousFocus.focus();
      }
      resolve(result);
    };
    const cancelValue = kind === 'prompt' ? null : kind === 'alert' ? true : false;
    const confirmValue = () => (kind === 'prompt' ? input.value : true);

    const onKeyDown = event => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      finish(cancelValue);
    };

    confirmButton.addEventListener('click', () => finish(confirmValue()));
    cancelButton?.addEventListener('click', () => finish(cancelValue));
    backdrop.addEventListener('click', () => finish(cancelValue));
    if (input) {
      input.addEventListener('keydown', event => {
        if (event.key === 'Enter') {
          event.preventDefault();
          finish(confirmValue());
        }
      });
    }
    document.addEventListener('keydown', onKeyDown, true);

    if (input) {
      input.focus();
      input.select();
    } else if (danger && cancelButton) {
      cancelButton.focus();
    } else {
      confirmButton.focus();
    }
  });
}

/** Modal confirmation. Resolves true only when the user confirms. */
export function confirmDialog(message, options = {}) {
  return _openDialog({
    kind: 'confirm',
    message,
    title: options.title ?? t('common.confirm_title'),
    confirmText: options.confirmText ?? t('common.confirm'),
    cancelText: options.cancelText ?? t('common.cancel'),
    danger: options.danger === true,
  }).then(result => result === true);
}

/** Modal notice with a single dismiss button. */
export function alertDialog(message, options = {}) {
  return _openDialog({
    kind: 'alert',
    message,
    title: options.title ?? t('common.alert_title'),
    confirmText: options.confirmText ?? t('common.confirm'),
  }).then(() => undefined);
}

/** Modal text input. Resolves the entered text, or null when cancelled. */
export function promptDialog(message, options = {}) {
  return _openDialog({
    kind: 'prompt',
    message,
    title: options.title ?? t('common.prompt_title'),
    value: options.value ?? '',
    placeholder: options.placeholder ?? '',
    confirmText: options.confirmText ?? t('common.confirm'),
    cancelText: options.cancelText ?? t('common.cancel'),
  });
}
