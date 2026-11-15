import { api } from './api.js';
import { attachCredentialFields, renderCredentialField } from './credential-field.js';
import { t } from './i18n.js';
import { html } from './utils.js';

export async function loadCredentials(root, onChange = () => {}) {
  try {
    const inventory = await api('GET', '/api/credentials');
    if (!inventory?.ok || !root.isConnected) return;
    root.innerHTML = html`<fieldset class="credentials-panel"><legend>${t('credentials.title')}</legend>
      <p class="credentials-intro">${t('credentials.intro')}</p>
      <div class="credentials-list">
      ${inventory.credentials.map(entry => html`<div class="credential-inventory-card">
        <div class="credential-inventory-header">
          <strong class="credential-inventory-label">${entry.label || entry.id}</strong>
          <span class="credential-inventory-kind">${entry.kind || 'credential'}</span>
        </div>
        <p class="credential-inventory-consumers">${entry.consumers.join(', ')}</p>
        ${renderCredentialField(entry.id)}</div>`)}
      </div>
    </fieldset>`;
    attachCredentialFields(root, inventory, async () => { await onChange(); await loadCredentials(root, onChange); });
  } catch { root.textContent = t('credentials.failed'); }
}
