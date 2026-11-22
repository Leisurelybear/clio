import { api } from './api.js';
import { confirmDialog } from './confirm.js';
import { t } from './i18n.js';
import { html } from './utils.js';

export function renderCredentialField(id) {
  return html`<div class="credential-field" data-credential-id="${id}">
    <div class="credential-field-main">
      <div class="credential-field-status">
        <span class="credential-status" data-credential-status>${t('credentials.loading')}</span>
        <span class="credential-value" data-credential-value aria-live="polite"></span>
      </div>
      <div class="credential-actions" data-credential-actions>
        <button class="credential-action" type="button" data-credential-replace>${t('credentials.replace')}</button>
        <button class="credential-action" type="button" data-credential-reveal>${t('credentials.reveal')}</button>
        <button class="credential-action" type="button" data-credential-migrate>${t('credentials.migrate')}</button>
        <button class="credential-action credential-action-danger" type="button" data-credential-delete>${t('credentials.delete')}</button>
      </div>
    </div>
    <span class="credential-error" data-credential-error role="status" aria-live="polite"></span>
  </div>`;
}

export async function saveCredential(id, value, metadata, source, confirmBinding = false) {
  const inventory = await api('GET', '/api/credentials');
  if (!inventory?.ok) throw new Error(t('credentials.failed'));
  const target = source || inventory.backends.default_source;
  const exists = inventory.credentials.some(entry => entry.id === id);
  const result = await api('PUT', `/api/credentials/${encodeURIComponent(id)}`, {
    value, source: target, ...(exists ? {} : { metadata }), ...(confirmBinding ? { confirm_binding: true } : {}),
  });
  if (!result?.ok) throw new Error(t('credentials.failed'));
  return result;
}

// One shared observer for every mounted credential field (instead of one
// body-wide subtree observer per field). Only removals can detach a field,
// so mutation batches without removed nodes (log appends, toasts) are skipped.
const _fieldDisposers = new Map();
let _fieldObserver = null;

function _watchFieldRemoval(field, dispose) {
  const previous = _fieldDisposers.get(field);
  if (previous) previous();
  _fieldDisposers.set(field, dispose);
  if (_fieldObserver || typeof MutationObserver === 'undefined') return;
  _fieldObserver = new MutationObserver(records => {
    if (!records.some(record => record.removedNodes.length)) return;
    for (const [node, disposeNode] of _fieldDisposers) {
      if (!node.isConnected) {
        _fieldDisposers.delete(node);
        disposeNode();
      }
    }
    if (!_fieldDisposers.size && _fieldObserver) {
      _fieldObserver.disconnect();
      _fieldObserver = null;
    }
  });
  _fieldObserver.observe(document.body, { childList: true, subtree: true });
}

/** Test hook: number of credential fields still being watched. */
export function _watchedCredentialFieldCount() {
  return _fieldDisposers.size;
}

export function attachCredentialFields(root, inventory, onChange = () => {}) {
  root.querySelectorAll('.credential-field').forEach(field => {
    const id = field.dataset.credentialId;
    const entry = inventory.credentials.find(item => item.id === id);
    const status = field.querySelector('[data-credential-status]');
    const revealed = field.querySelector('[data-credential-value]');
    const error = field.querySelector('[data-credential-error]');
    const source = entry?.effective_source || t('credentials.none');
    const deleteSource = ['dotenv', 'keyring'].includes(entry?.effective_source)
      ? entry.effective_source
      : (inventory.backends.default_source || 'dotenv');
    status.textContent = `${t(entry?.configured ? 'credentials.configured' : 'credentials.missing')} · ${source}`
      + (entry?.shadowed_sources?.length ? ` · ${t('credentials.shadowed')}: ${entry.shadowed_sources.join(', ')}` : '');
    let timer;
    let disposed = false;
    const clear = () => { clearTimeout(timer); revealed.textContent = ''; };
    const onVisibility = () => { if (document.hidden) clear(); };
    document.addEventListener('visibilitychange', onVisibility);
    _watchFieldRemoval(field, () => {
      disposed = true;
      clear();
      document.removeEventListener('visibilitychange', onVisibility);
    });
    async function run(action) {
      error.textContent = '';
      clear();
      try { await action(); } catch { if (!disposed) error.textContent = t('credentials.failed'); }
    }
    field.querySelector('[data-credential-reveal]').onclick = () => run(async () => {
      if (!await confirmDialog(t(entry?.effective_source === 'environment' ? 'credentials.reveal_environment' : 'credentials.reveal_confirm'))) return;
      const result = await api('POST', `/api/credentials/${encodeURIComponent(id)}/reveal`, { confirmed: true });
      if (!result?.ok) throw new Error();
      if (disposed || !field.isConnected) return;
      revealed.textContent = result.value;
      result.value = '';
      timer = setTimeout(clear, 30000);
    });
    field.querySelector('[data-credential-replace]').onclick = () => {
      const dialog = document.createElement('div');
      dialog.className = 'modal';
      dialog.innerHTML = html`<div class="modal-backdrop"></div><div class="modal-dialog credential-modal">
        <label><span>${t('credentials.replace')}</span><input type="password" autocomplete="new-password" data-new-value></label>
        <label><span>${t('credentials.store_in')}</span><select data-source><option value="dotenv">${t('credentials.dotenv')}</option>
          <option value="keyring">${t('credentials.keyring')}</option></select></label>
        <div class="modal-actions"><button class="btn-secondary" type="button" data-cancel>${t('common.cancel')}</button><button class="btn-primary" type="button" data-save>${t('common.save')}</button></div>
        <span data-error role="status"></span></div>`;
      document.body.appendChild(dialog);
      const input = dialog.querySelector('[data-new-value]');
      const select = dialog.querySelector('[data-source]');
      select.value = inventory.backends.default_source;
      select.querySelector('[value="keyring"]').disabled = !inventory.backends.keyring.available;
      const close = () => { input.value = ''; dialog.remove(); };
      // Global Escape (modal-escape.js) must run the same cleanup.
      dialog._closeModal = close;
      dialog.querySelector('[data-cancel]').onclick = close;
      dialog.querySelector('.modal-backdrop').onclick = close;
      dialog.querySelector('[data-save]').onclick = async () => {
        if (!input.value) return;
        const btn = dialog.querySelector('[data-save]');
        btn.disabled = true;
        try {
          const result = await saveCredential(id, input.value, undefined, select.value);
          close();
          await onChange(result);
        } catch { dialog.querySelector('[data-error]').textContent = t('credentials.failed'); }
        finally { btn.disabled = false; }
      };
    };
    field.querySelector('[data-credential-migrate]').hidden = !entry?.migration_needed || !entry?.configured;
    field.querySelector('[data-credential-migrate]').onclick = () => run(async () => {
      if (!await confirmDialog(t('credentials.migrate_confirm'))) return;
      const result = await api('POST', `/api/credentials/${encodeURIComponent(id)}/migrate`, { confirmed: true });
      if (!result?.ok) throw new Error();
      await onChange(result);
    });
    field.querySelector('[data-credential-delete]').onclick = () => run(async () => {
      if (!await confirmDialog(t('credentials.delete_confirm'), { danger: true })) return;
      const result = await api('PUT', `/api/credentials/${encodeURIComponent(id)}`, { value: '', source: deleteSource, confirmed: true });
      if (!result?.ok) throw new Error();
      await onChange(result);
    });
  });
}
