/**
 * Provider list rendering, modal editing, connection tests, and handlers.
 */
import { api, icon } from './api.js';
import { confirmDialog } from './confirm.js';
import { t } from './i18n.js';
import { state } from './state.js';
import { escapeHtml, markDirty, setStatus } from './utils.js';

const DEFAULT_PROVIDERS = ['gemini', 'openai', 'deepseek'];

export function providerTypeLabel(type, long = false) {
  if (type === 'ollama') {
    return t(long ? 'settings.provider.type.ollama_long' : 'settings.provider.type.ollama');
  }
  if (type === 'gemini') {
    return t(long ? 'settings.provider.type.gemini_long' : 'settings.provider.type.gemini');
  }
  return t(long ? 'settings.provider.type.openai_compat_long' : 'settings.provider.type.openai_compat');
}
const FNV_OFFSET_BASIS = 0x811c9dc5;
const FNV_PRIME = 0x01000193;
const NON_ASCII_RE = /[^\x00-\x7F]/;

function _fnv1aCodePoints(value) {
  let hash = FNV_OFFSET_BASIS;
  for (const char of value) {
    hash ^= char.codePointAt(0);
    hash = Math.imul(hash, FNV_PRIME);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

export function providerEnvName(name) {
  const trimmed = String(name || '').trim();
  const slug = trimmed
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
  const base = slug || 'CUSTOM_PROVIDER';
  if (NON_ASCII_RE.test(trimmed)) {
    return `${base}_${_fnv1aCodePoints(trimmed)}_API_KEY`;
  }
  return `${base}_API_KEY`;
}

export const DEFAULT_MODELS = {
  gemini: ['gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-3-flash', 'gemini-3.1-flash-lite', 'gemini-3.5-flash'],
  openai: ['gpt-4o', 'gpt-4o-mini'],
  deepseek: ['deepseek-chat', 'deepseek-reasoner', 'deepseek-v4-flash', 'deepseek-v4-pro'],
};
const DEFAULT_CAPABILITIES = {
  gemini: ['video', 'text'],
  openai: ['text'],
};

export function providerCapabilities(provider) {
  if (provider?.capabilities?.length) return provider.capabilities;
  return DEFAULT_CAPABILITIES[provider?.type] || ['text'];
}

export function renderTagInput(container, values, onChange) {
  const wrapper = document.createElement('div');
  wrapper.className = 'tag-input-wrapper';

  const chips = document.createElement('div');
  chips.className = 'tag-chips';

  function renderChips() {
    chips.innerHTML = '';
    for (const v of values) {
      const chip = document.createElement('span');
      chip.className = 'tag-chip';
      chip.innerHTML = `${escapeHtml(v)} <span class="tag-chip-remove">×</span>`;
      chip.querySelector('.tag-chip-remove').onclick = () => {
        const idx = values.indexOf(v);
        if (idx >= 0) values.splice(idx, 1);
        renderChips();
        onChange(values);
      };
      chips.appendChild(chip);
    }
  }
  renderChips();

  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'tag-input';
  input.placeholder = t('settings.tags.placeholder');
  input.onkeydown = (e) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      const val = input.value.trim();
      if (val && !values.includes(val)) {
        values.push(val);
        renderChips();
        onChange(values);
      }
      input.value = '';
    } else if (e.key === 'Backspace' && !input.value && values.length) {
      values.pop();
      renderChips();
      onChange(values);
    }
  };

  wrapper.appendChild(chips);
  wrapper.appendChild(input);
  container.appendChild(wrapper);
}


export function renderProviderList(providers, descs) {
  const providersObj = providers || {};
  const keys = Object.keys(providersObj);
  let html = `<fieldset class="config-fieldset"><legend>${t('settings.ai_model_list')}</legend>`;

  if (keys.length === 0) {
    html += `<div class="config-empty-state"><p class="muted">${t('settings.provider.empty_title')}</p><p class="hint">${t('settings.provider.empty_hint')}</p></div>`;
  } else {
    html += `<p class="hint" style="margin:0 0 8px;line-height:1.6">${t('settings.provider.intro')}</p>`;
  }

  for (const name of keys) {
    const p = providersObj[name];
    const typeLabel = providerTypeLabel(p.type);
    const hasModels = p.models && p.models.length > 0;
    const modelTags = hasModels
      ? p.models.map(m => `<span class="tag-chip">${escapeHtml(m)}</span>`).join(' ')
      : `<span class="warn">⚠️ ${t('settings.provider.no_models_badge')}</span>`;
    const capabilityTags = providerCapabilities(p)
      .map(c => `<span class="tag-chip">${escapeHtml(c)}</span>`)
      .join(' ');
    const promptPrice = Number(p.prompt_price_per_1m_usd || 0);
    const completionPrice = Number(p.completion_price_per_1m_usd || 0);
    const priceField = promptPrice > 0 || completionPrice > 0
      ? `<div class="provider-card-field"><span class="provider-card-label">${t('settings.provider.field.price')}</span><span>${escapeHtml(t('settings.provider.field.price_summary', { prompt: String(promptPrice), completion: String(completionPrice) }))}</span></div>`
      : '';

    html += `<div class="provider-card" data-provider="${escapeHtml(name)}">
      <div class="provider-card-header">
        <span class="provider-card-name">${escapeHtml(name)}</span>
        <span class="provider-card-type">${typeLabel}</span>
        <span class="provider-card-actions">
          <button class="btn-provider-test" data-provider="${escapeHtml(name)}">${t('settings.provider.test')}</button>
          <button class="btn-provider-edit" data-provider="${escapeHtml(name)}">${t('common.edit')}</button>
          ${DEFAULT_PROVIDERS.includes(name) ? '' : `<button class="btn-provider-delete" data-provider="${escapeHtml(name)}">${t('common.delete')}</button>`}
        </span>
      </div>
      <div class="provider-card-body">
        <div class="provider-card-field"><span class="provider-card-label">${t('settings.provider.field.type')}</span><span>${typeLabel}</span></div>
        <div class="provider-card-field"><span class="provider-card-label">${t('settings.provider.field.api_key')}</span><span class="provider-key-wrap" data-provider="${escapeHtml(name)}"><span class="provider-key-masked">••••••••••</span><span class="provider-key-value" style="display:none"></span><button class="btn-provider-show-key">${t('settings.provider.show_key')}</button></span></div>
        ${p.type !== 'gemini' ? `<div class="provider-card-field"><span class="provider-card-label">${t('settings.provider.field.base_url')}</span><span>${escapeHtml(p.base_url || t('settings.provider.field.default_value'))}</span></div>` : ''}
        <div class="provider-card-field"><span class="provider-card-label">${t('settings.provider.field.capabilities')}</span><span class="provider-card-models">${capabilityTags}</span></div>
        <div class="provider-card-field"><span class="provider-card-label">${t('settings.provider.field.models')}</span><span class="provider-card-models">${modelTags}</span></div>
        <div class="provider-card-field"><span class="provider-card-label">max_tokens</span><span>${p.max_tokens === 0 || p.max_tokens == null ? t('settings.provider.field.unlimited') : escapeHtml(String(p.max_tokens))}</span></div>
        ${p.type === 'gemini' ? `<div class="provider-card-field"><span class="provider-card-label">video_upload_max_mb</span><span>${escapeHtml(String(p.video_upload_max_mb ?? 200))} MB</span></div>` : ''}
        <div class="provider-card-field"><span class="provider-card-label">timeout</span><span>${escapeHtml(String(p.timeout_sec ?? 120))}s</span></div>
        ${priceField}
        <div class="provider-test-status" data-provider="${escapeHtml(name)}"></div>
      </div>
    </div>`;
  }

  html += `<div style="margin-top:12px"><button id="btn-add-provider" class="btn-primary">${icon('plus', 14)} ${t('settings.provider.add')}</button></div>`;
  html += '</fieldset>';
  return html;
}

function _showProviderModal(providersObj, name, onSave) {
  const existing = name ? providersObj[name] : null;
  const isEdit = !!existing;
  const backdrop = document.createElement('div');
  backdrop.className = 'modal';
  backdrop.style.display = 'flex';

  const typeOptions = ['gemini', 'openai', 'ollama'].map(type =>
    `<option value="${type}"${existing?.type === type ? ' selected' : ''}>${providerTypeLabel(type, true)}</option>`
  ).join('');

  backdrop.innerHTML = `
    <div class="modal-backdrop"></div>
    <div class="modal-dialog" style="max-width:520px">
      <h3 style="margin:0 0 var(--space-4)">${isEdit ? t('settings.provider.edit_title') : t('settings.provider.add_title')}</h3>
      <div class="form-group">
        <label class="form-label">${t('settings.provider.field.name')} <span class="muted">${t('settings.provider.field.name_hint')}</span></label>
        <input id="modal-provider-name" class="form-input" value="${escapeHtml(name || '')}" ${isEdit ? 'readonly' : ''} placeholder="${t('settings.provider.field.name_placeholder')}">
      </div>
      <div class="form-group">
        <label class="form-label">${t('settings.provider.field.type')}</label>
        <select id="modal-provider-type" class="form-input">${typeOptions}</select>
        <span class="hint">${t('settings.provider.field.type_hint')}</span>
      </div>
      <div class="form-group">
        <label class="form-label">${t('settings.provider.field.api_key')}</label>
        <div style="display:flex;gap:8px">
          <input id="modal-provider-key" class="form-input" type="password" placeholder="${t('settings.provider.field.key_placeholder')}" style="flex:1">
          <button id="modal-key-toggle" class="btn-secondary">${t('settings.provider.show_key')}</button>
        </div>
        <span class="hint">${t('settings.provider.field.key_hint')}${isEdit ? t('settings.provider.field.key_hint_edit') : ''}</span>
      </div>
      <div class="form-group" id="modal-base-url-group" style="${existing?.type === 'gemini' && !isEdit ? 'display:none' : ''}">
        <label class="form-label">${t('settings.provider.field.base_url')}</label>
        <input id="modal-provider-base-url" class="form-input" value="${escapeHtml(existing?.base_url || '')}" placeholder="https://api.openai.com/v1">
        <span class="hint">${t('settings.provider.field.base_url_hint')}</span>
      </div>
      <div class="form-group">
        <label class="form-label">${t('settings.provider.field.models_label')} <span class="muted">${t('settings.provider.field.enter_to_add')}</span></label>
        <div id="modal-provider-models"></div>
        <span class="hint">${t('settings.provider.field.models_hint')}</span>
      </div>
      <div class="form-group">
        <label class="form-label">${t('settings.provider.field.capabilities_label')} <span class="muted">${t('settings.provider.field.enter_to_add')}</span></label>
        <div id="modal-provider-capabilities"></div>
        <span class="hint">${t('settings.provider.field.capabilities_hint')}</span>
      </div>
      <div class="form-group" style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div>
          <label class="form-label">max_tokens</label>
          <input id="modal-provider-max-tokens" class="form-input" type="number" min="0" step="1"
            value="${existing?.max_tokens ?? 0}">
          <span class="hint">${t('settings.provider.field.max_tokens_hint')}</span>
        </div>
        <div>
          <label class="form-label">timeout_sec</label>
          <input id="modal-provider-timeout" class="form-input" type="number" min="0" step="1"
            value="${existing?.timeout_sec ?? 120}">
          <span class="hint">${t('settings.provider.field.timeout_hint')}</span>
        </div>
        <div>
          <label class="form-label">retry_attempts</label>
          <input id="modal-provider-retry" class="form-input" type="number" min="0" step="1"
            value="${existing?.retry_attempts ?? 2}">
          <span class="hint">${t('settings.provider.field.retry_hint')}</span>
        </div>
        <div>
          <label class="form-label">requests_per_minute</label>
          <input id="modal-provider-rpm" class="form-input" type="number" min="0" step="1"
            value="${existing?.requests_per_minute ?? 0}">
          <span class="hint">${t('settings.provider.field.rpm_hint')}</span>
        </div>
        <div id="modal-poll-group" style="${(existing?.type || 'gemini') === 'gemini' ? '' : 'display:none'}">
          <label class="form-label">poll_interval_sec</label>
          <input id="modal-provider-poll" class="form-input" type="number" min="0" step="1"
            value="${existing?.poll_interval_sec ?? 5}">
          <span class="hint">${t('settings.provider.field.poll_hint')}</span>
        </div>
        <div id="modal-video-upload-group" style="${(existing?.type || 'gemini') === 'gemini' ? '' : 'display:none'}">
          <label class="form-label">video_upload_max_mb</label>
          <input id="modal-provider-upload-max" class="form-input" type="number" min="1" step="1"
            value="${existing?.video_upload_max_mb ?? 200}">
          <span class="hint">${t('settings.provider.field.video_upload_max_mb_hint')}</span>
        </div>
        <div>
          <label class="form-label">prompt_price_per_1m_usd</label>
          <input id="modal-provider-prompt-price" class="form-input" type="number" min="0" step="0.01"
            value="${existing?.prompt_price_per_1m_usd ?? 0}">
          <span class="hint">${t('settings.provider.field.prompt_price_hint')}</span>
        </div>
        <div>
          <label class="form-label">completion_price_per_1m_usd</label>
          <input id="modal-provider-completion-price" class="form-input" type="number" min="0" step="0.01"
            value="${existing?.completion_price_per_1m_usd ?? 0}">
          <span class="hint">${t('settings.provider.field.completion_price_hint')}</span>
        </div>
      </div>
      <div class="modal-actions">
        <button id="modal-cancel" class="btn-secondary">${t('common.cancel')}</button>
        <button id="modal-save" class="btn-primary">${isEdit ? t('settings.provider.save_edit') : t('settings.provider.modal_add')}</button>
      </div>
    </div>
  `;
  document.body.appendChild(backdrop);

  // Tag input for models — pre-populate defaults for known providers
  const modelsContainer = backdrop.querySelector('#modal-provider-models');
  let modelsList;
  if (existing) {
    modelsList = existing.models?.length ? [...existing.models] : [...(DEFAULT_MODELS[name] || [])];
  } else {
    modelsList = [];
  }
  _renderTagInput(modelsContainer, modelsList, () => {});
  const capabilitiesContainer = backdrop.querySelector('#modal-provider-capabilities');
  let capabilitiesList = existing
    ? [...providerCapabilities(existing)]
    : [...providerCapabilities({ type: backdrop.querySelector('#modal-provider-type').value })];
  function renderCapabilitiesInput() {
    capabilitiesContainer.innerHTML = '';
    _renderTagInput(capabilitiesContainer, capabilitiesList, () => {});
  }
  renderCapabilitiesInput();

  // Type toggle → show/hide base URL + poll interval
  backdrop.querySelector('#modal-provider-type').onchange = (e) => {
    const isGemini = e.target.value === 'gemini';
    backdrop.querySelector('#modal-base-url-group').style.display = isGemini ? 'none' : '';
    const pollGrp = backdrop.querySelector('#modal-poll-group');
    if (pollGrp) pollGrp.style.display = isGemini ? '' : 'none';
    const uploadGrp = backdrop.querySelector('#modal-video-upload-group');
    if (uploadGrp) uploadGrp.style.display = isGemini ? '' : 'none';
    if (!isEdit) {
      capabilitiesList = [...providerCapabilities({ type: e.target.value })];
      renderCapabilitiesInput();
    }
  };

  // Key visibility toggle
  const keyInput = backdrop.querySelector('#modal-provider-key');
  backdrop.querySelector('#modal-key-toggle').onclick = () => {
    const isPwd = keyInput.type === 'password';
    keyInput.type = isPwd ? 'text' : 'password';
    backdrop.querySelector('#modal-key-toggle').textContent = isPwd ? t('settings.provider.hide_key') : t('settings.provider.show_key');
  };

  // Save
  backdrop.querySelector('#modal-save').onclick = async () => {
    const newName = backdrop.querySelector('#modal-provider-name').value.trim();
    const newType = backdrop.querySelector('#modal-provider-type').value;
    const newKey = backdrop.querySelector('#modal-provider-key').value.trim();
    const newBaseUrl = backdrop.querySelector('#modal-provider-base-url').value.trim();
    const parseNum = (id, fallback) => {
      const raw = backdrop.querySelector(id)?.value;
      if (raw === undefined || raw === null || String(raw).trim() === '') return fallback;
      const n = Number(raw);
      return Number.isFinite(n) ? n : fallback;
    };
    const newMaxTokens = parseNum('#modal-provider-max-tokens', 0);
    const newTimeout = parseNum('#modal-provider-timeout', 120);
    const newRetry = parseNum('#modal-provider-retry', 2);
    const newRpm = parseNum('#modal-provider-rpm', 0);
    const newPoll = parseNum('#modal-provider-poll', 5);
    const newUploadMax = parseNum('#modal-provider-upload-max', 200);
    const newPromptPrice = parseNum('#modal-provider-prompt-price', 0);
    const newCompletionPrice = parseNum('#modal-provider-completion-price', 0);

    if (!newName) { setStatus(t('settings.provider.name_required'), 'warn'); return; }
    if (isEdit && newName !== name) { setStatus(t('settings.provider.name_immutable'), 'warn'); return; }
    if (!isEdit && providersObj[newName]) { setStatus(t('settings.provider.name_exists', { name: newName }), 'warn'); return; }
    if (newMaxTokens < 0 || newTimeout < 0 || newRetry < 0 || newRpm < 0 || newPoll < 0
        || newPromptPrice < 0 || newCompletionPrice < 0) {
      setStatus(t('settings.provider.negative_numbers'), 'warn');
      return;
    }
    if (newType === 'gemini' && newUploadMax < 1) {
      setStatus(t('settings.provider.upload_limit_positive'), 'warn');
      return;
    }

    // Save API key to .env if provided
    if (newKey) {
      const envNameEnv = providerEnvName(newName);
      try {
        const existing = await api('GET', '/api/env');
        let envContent = existing.content || '';
        const lines = envContent.split('\n');
        let found = false;
        for (let i = 0; i < lines.length; i++) {
          if (lines[i].startsWith(envNameEnv + '=')) {
            lines[i] = `${envNameEnv}=${newKey}`;
            found = true;
            break;
          }
        }
        if (!found) lines.push(`${envNameEnv}=${newKey}`);
        const envResult = await api('PUT', '/api/env', { content: lines.join('\n') });
        if (!envResult.ok) { setStatus(t('settings.provider.key_save_failed', { detail: envResult.error || '' }), 'err'); return; }
      } catch (e) { setStatus(t('settings.provider.key_save_failed', { detail: e.message }), 'err'); return; }
    }

    const envName = providerEnvName(newName);
    const providerData = {
      type: newType,
      api_key_env: envName,
      api_key: '',
      base_url: newBaseUrl || '',
      models: modelsList,
      capabilities: capabilitiesList,
      max_tokens: newMaxTokens,
      video_upload_max_mb: newUploadMax,
      prompt_price_per_1m_usd: newPromptPrice,
      completion_price_per_1m_usd: newCompletionPrice,
      timeout_sec: newTimeout,
      retry_attempts: newRetry,
      requests_per_minute: newRpm,
      poll_interval_sec: newPoll,
    };

    if (isEdit) {
      if (!newKey) {
        delete providerData.api_key_env;
        delete providerData.api_key;
      }
      Object.assign(providersObj[name], providerData);
    } else {
      providersObj[newName] = {
        name: newName,
        type: newType,
        api_key_env: envName,
        api_key: '',
        base_url: newBaseUrl || '',
        models: [...modelsList],
        capabilities: [...capabilitiesList],
        max_tokens: newMaxTokens,
        video_upload_max_mb: newUploadMax,
        prompt_price_per_1m_usd: newPromptPrice,
        completion_price_per_1m_usd: newCompletionPrice,
        timeout_sec: newTimeout,
        retry_attempts: newRetry,
        requests_per_minute: newRpm,
        poll_interval_sec: newPoll,
      };
    }

    markDirty();
    backdrop.remove();
    onSave();
  };

  backdrop.querySelector('#modal-cancel').onclick = () => backdrop.remove();
  backdrop.querySelector('.modal-backdrop').onclick = () => backdrop.remove();
}

function _escapeCssAttributeValue(value) {
  return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\A ');
}

function _chooseProviderTestModel(provider) {
  const models = provider?.models || [];
  if (models.length === 0) return { ok: false, error: t('settings.provider.test_need_models') };
  if (models.length === 1) return { ok: true, model: models[0] };

  return new Promise(resolve => {
    const backdrop = document.createElement('div');
    backdrop.className = 'modal';
    backdrop.setAttribute('role', 'dialog');
    backdrop.setAttribute('aria-modal', 'true');
    const options = models
      .map(model => `<option value="${escapeHtml(model)}">${escapeHtml(model)}</option>`)
      .join('');
    backdrop.innerHTML = `
      <div class="modal-backdrop"></div>
      <div class="modal-dialog" role="document">
        <h3>${escapeHtml(t('settings.provider.test_pick_model'))}</h3>
        <label>
          <span>${escapeHtml(t('settings.provider.test_model_label'))}</span>
          <select class="form-input" data-provider-test-model>${options}</select>
        </label>
        <div class="modal-actions">
          <button type="button" class="btn-secondary" data-provider-test-cancel>${escapeHtml(t('common.cancel'))}</button>
          <button type="button" class="btn-primary" data-provider-test-confirm>${escapeHtml(t('common.confirm'))}</button>
        </div>
      </div>`;
    document.body.appendChild(backdrop);
    const select = backdrop.querySelector('[data-provider-test-model]');
    const finish = result => {
      document.removeEventListener('keydown', onKeyDown);
      backdrop.remove();
      resolve(result);
    };
    const cancel = () => finish({ ok: false, error: t('settings.provider.test_canceled'), canceled: true });
    const onKeyDown = event => {
      if (event.key === 'Escape') cancel();
    };
    backdrop.querySelector('[data-provider-test-cancel]').addEventListener('click', cancel);
    backdrop.querySelector('[data-provider-test-confirm]').addEventListener('click', () => {
      const model = String(select?.value || '').trim();
      if (!models.includes(model)) {
        finish({ ok: false, error: t('settings.provider.test_invalid_model') });
        return;
      }
      finish({ ok: true, model });
    });
    backdrop.querySelector('.modal-backdrop').addEventListener('click', cancel);
    document.addEventListener('keydown', onKeyDown);
    select?.focus();
  });
}

export async function testProvider(providersObj, providerName) {
  const provider = providersObj?.[providerName];
  const choice = await _chooseProviderTestModel(provider);
  if (!choice.ok) return choice;

  return api('POST', '/api/ai/test', {
    provider: providerName,
    model: choice.model,
  });
}

export function attachProviderListHandlers(pane, providersObj, reRender = () => {}) {

  // Test buttons
  pane.querySelectorAll('.btn-provider-test').forEach(btn => {
    btn.onclick = async () => {
      const providerName = btn.dataset.provider;
      const card = btn.closest('.provider-card');
      const status = card?.querySelector('.provider-test-status');
      btn.disabled = true;
      if (status) {
        status.className = 'provider-test-status';
        status.textContent = t('settings.provider.testing');
      }
      try {
        const result = await testProvider(providersObj, providerName);
        if (result?.canceled) {
          const message = result.error || t('settings.provider.test_canceled');
          if (status) status.textContent = message;
          void setStatus(message, 'warn');
          return;
        }
        if (result?.ok) {
          const message = t('settings.provider.test_ok', { ms: result.elapsed_ms ?? '?' });
          if (status) {
            status.className = 'provider-test-status ok';
            status.textContent = message;
          }
          void setStatus(message, 'ok');
        } else {
          const message = t('settings.provider.test_failed', { detail: result?.error || t('common.unknown_error') });
          if (status) {
            status.className = 'provider-test-status err';
            status.textContent = message;
          }
          void setStatus(message, 'err');
        }
      } catch (e) {
        const message = t('settings.provider.test_failed', { detail: e.message || e });
        if (status) {
          status.className = 'provider-test-status err';
          status.textContent = message;
        }
        void setStatus(message, 'err');
      } finally {
        btn.disabled = false;
      }
    };
  });

  // Edit buttons
  pane.querySelectorAll('.btn-provider-edit').forEach(btn => {
    btn.onclick = () => {
      const name = btn.dataset.provider;
      _showProviderModal(providersObj, name, reRender);
    };
  });

  // Delete buttons
  pane.querySelectorAll('.btn-provider-delete').forEach(btn => {
    btn.onclick = async () => {
      const name = btn.dataset.provider;
      const projectTasks = state.configProject?.ai?.tasks || {};
      const defaultTasks = state.configGlobal?.ai?.default_tasks || {};
      const refs = [
        ...Object.entries(projectTasks)
          .filter(([_, task]) => task.provider === name)
          .map(([k]) => t('settings.provider.ref_project', { name: k })),
        ...Object.entries(defaultTasks)
          .filter(([_, task]) => task.provider === name)
          .map(([k]) => t('settings.provider.ref_global', { name: k })),
      ];
      if (refs.length > 0) {
        if (!(await confirmDialog(t('settings.provider.delete_in_use', { name, refs: refs.join(t('common.list_separator')) }), { danger: true }))) return;
      } else {
        if (!(await confirmDialog(t('settings.provider.delete_confirm', { name }), { danger: true }))) return;
      }
      delete providersObj[name];
      markDirty();
      reRender();
    };
  });

  // Add button
  const addBtn = pane.querySelector('#btn-add-provider');
  if (addBtn) {
    addBtn.onclick = () => _showProviderModal(providersObj, null, reRender);
  }

  // Show/hide key buttons
  pane.querySelectorAll('.btn-provider-show-key').forEach(btn => {
    btn.onclick = () => {
      const wrap = btn.closest('.provider-key-wrap');
      if (!wrap) return;
      const masked = wrap.querySelector('.provider-key-masked');
      const value = wrap.querySelector('.provider-key-value');
      const isVisible = value?.style.display !== 'none';
      if (isVisible) {
        value.style.display = 'none';
        masked.style.display = '';
        btn.textContent = t('settings.provider.show_key');
      } else {
        value.textContent = t('settings.provider.key_not_exposed');
        value.style.display = '';
        masked.style.display = 'none';
        btn.textContent = t('settings.provider.hide_key');
      }
    };
  });
}
