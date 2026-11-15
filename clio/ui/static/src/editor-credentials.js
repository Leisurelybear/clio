import { api } from './api.js';
import { attachCredentialFields, renderCredentialField } from './credential-field.js';
import { t } from './i18n.js';
import { html } from './utils.js';

export async function loadCredentials(root, onChange = () => {}) {
  try {
    const inventory = await api('GET', '/api/credentials');
    if (!inventory?.ok || !root.isConnected) return;
    root.innerHTML = html`<fieldset><legend>${t('credentials.title')}</legend>
      <p>${t('credentials.intro')}</p>
      ${inventory.credentials.map(entry => html`<div class="config-card">
        <strong>${entry.label || entry.id}</strong><p>${entry.consumers.join(', ')}</p>
        ${renderCredentialField(entry.id)}</div>`)}
    </fieldset>`;
    attachCredentialFields(root, inventory, async () => { await onChange(); await loadCredentials(root, onChange); });
  } catch { root.textContent = t('credentials.failed'); }
}
