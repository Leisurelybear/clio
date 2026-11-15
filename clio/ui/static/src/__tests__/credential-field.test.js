import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api.js';
import { confirmDialog } from '../confirm.js';
import { attachCredentialFields, renderCredentialField, saveCredential } from '../credential-field.js';
import { loadCredentials } from '../editor-credentials.js';

vi.mock('../api.js', () => ({ api: vi.fn() }));
vi.mock('../confirm.js', () => ({ confirmDialog: vi.fn() }));

const inventory = {
  ok: true,
  credentials: [{ id: 'gemini_api_key', label: 'Gemini', configured: true, effective_source: 'environment',
    shadowed_sources: ['dotenv'], consumers: ['ai.providers.gemini'], migration_needed: true }],
  backends: { default_source: 'dotenv', keyring: { available: false } },
};

beforeEach(() => { vi.resetAllMocks(); confirmDialog.mockResolvedValue(true); });
afterEach(() => { document.body.innerHTML = ''; vi.useRealTimers(); });

function mount(data = inventory) {
  const root = document.createElement('div');
  root.innerHTML = renderCredentialField('gemini_api_key');
  document.body.appendChild(root);
  attachCredentialFields(root, data);
  return root;
}

describe('credential fields', () => {
  it('shows source and shadowing without rendering any value', () => {
    const root = mount();
    expect(root.textContent).toContain('environment');
    expect(root.textContent).toContain('dotenv');
    expect(root.querySelector('[data-credential-value]').textContent).toBe('');
    expect(root.querySelector('input')).toBeNull();
  });

  it('reveals only after confirmation and clears after 30 seconds', async () => {
    vi.useFakeTimers();
    api.mockResolvedValue({ ok: true, value: '<secret>' });
    const root = mount();
    root.querySelector('[data-credential-reveal]').click();
    await vi.waitFor(() => expect(root.querySelector('[data-credential-value]').textContent).toBe('<secret>'));
    expect(root.querySelector('secret')).toBeNull();
    expect(confirmDialog.mock.calls[0][0]).toContain('进程环境');
    expect(api).toHaveBeenCalledWith('POST', '/api/credentials/gemini_api_key/reveal', { confirmed: true });
    await vi.advanceTimersByTimeAsync(30000);
    expect(root.querySelector('[data-credential-value]').textContent).toBe('');
  });

  it('does not reveal after the field has been removed', async () => {
    let finish;
    api.mockReturnValue(new Promise(resolve => { finish = resolve; }));
    const root = mount();
    root.querySelector('[data-credential-reveal]').click();
    await vi.waitFor(() => expect(api).toHaveBeenCalled());
    root.remove();
    finish({ ok: true, value: 'secret' });
    await Promise.resolve();
    expect(root.querySelector('[data-credential-value]').textContent).toBe('');
  });

  it('does not call reveal when confirmation is canceled', async () => {
    confirmDialog.mockResolvedValue(false);
    const root = mount();
    root.querySelector('[data-credential-reveal]').click();
    await Promise.resolve();
    expect(api).not.toHaveBeenCalled();
  });

  it('replaces a single field using the preferred writable backend', async () => {
    api.mockImplementation(async (method) => method === 'GET' ? inventory : { ok: true, credential: inventory.credentials[0] });
    const root = mount();
    root.querySelector('[data-credential-replace]').click();
    const input = document.querySelector('[data-new-value]');
    input.value = 'new-secret';
    document.querySelector('[data-save]').click();
    await vi.waitFor(() => expect(api).toHaveBeenCalledWith('PUT', '/api/credentials/gemini_api_key', {
      value: 'new-secret', source: 'dotenv',
    }));
    expect(api.mock.calls.some(([, url]) => url === '/api/env')).toBe(false);
    expect(input.value).toBe('');
  });

  it('confirms migration and selected-backend deletion', async () => {
    api.mockResolvedValue({ ok: true });
    const root = mount();
    root.querySelector('[data-credential-migrate]').click();
    await vi.waitFor(() => expect(api).toHaveBeenCalledWith('POST', '/api/credentials/gemini_api_key/migrate', { confirmed: true }));
    root.querySelector('[data-credential-delete]').click();
    await vi.waitFor(() => expect(api).toHaveBeenCalledWith('PUT', '/api/credentials/gemini_api_key', {
      value: '', source: 'dotenv', confirmed: true,
    }));
  });

  it('creates metadata only for a new credential', async () => {
    api.mockResolvedValueOnce({ ...inventory, credentials: [] }).mockResolvedValueOnce({ ok: true });
    await saveCredential('test_key', 'secret', { label: 'test' });
    expect(api).toHaveBeenLastCalledWith('PUT', '/api/credentials/test_key', {
      source: 'dotenv', value: 'secret', metadata: { label: 'test' },
    });
  });

  it('renders inventory labels and consumers with escaping', async () => {
    api.mockResolvedValue({ ...inventory, credentials: [{ ...inventory.credentials[0], label: '<img src=x>', consumers: ['<script>'] }] });
    const root = document.createElement('div');
    document.body.appendChild(root);
    await loadCredentials(root);
    expect(root.querySelector('img')).toBeNull();
    expect(root.querySelector('script')).toBeNull();
    expect(root.textContent).toContain('<img src=x>');
    expect(root.querySelector('[data-credential-reveal]')).not.toBeNull();
  });
});
