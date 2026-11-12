import { state } from './state.js';
import { $, clearDirty, setStatus } from './utils.js';
import { api, icon } from './api.js';
import { setBrowseButtonsVisible } from './desktop-pick.js';
import {
  DEFAULT_MODELS,
  DEFAULT_PROVIDERS,
  attachProviderListHandlers,
  providerCapabilities,
  renderProviderList,
  renderTagInput,
  testProvider,
} from './editor-providers.js';
import { attachTaskBindingHandlers, renderTaskBinding } from './editor-task-binding.js';
import { renderLogs, stopLogsPolling } from './editor-logs.js';
import { loadModelManagement, renderModelManagement } from './editor-models.js';
import { renderTokens } from './editor-tokens.js';
import { loadPromptManagement, renderPromptManagement } from './editor-prompts.js';
import { onLocaleChange, t } from './i18n.js';
import { confirmDialog } from './confirm.js';
import { ensureGlobalUiDefaults } from './language-switcher.js';
import {
  _attachConfigForm,
  _attachContextTemplate,
  _isAdvancedVisible,
  _loadConfigSchema,
  _renderConfigForm,
  _renderConfigGlobal,
  _renderConfigProject,
  _renderTooltip,
  _setAdvancedVisible,
  setSectionCollapsed,
} from './editor-config-form.js';
export {
  FIELD_LABELS,
  descFromPath,
  labelFromPath,
  optionLabel,
  pathPickKind,
} from './editor-config-form.js';
export {
  _attachConfigForm,
  _attachContextTemplate,
  _isAdvancedVisible,
  _loadConfigSchema,
  _renderConfigForm,
  _renderConfigGlobal,
  _renderConfigProject,
  _renderTooltip,
  _setAdvancedVisible,
};

export { renderLogs, renderTokens, stopLogsPolling };
export { providerEnvName, providerTypeLabel } from './editor-providers.js';
export { mergeTaskBindings, taskDescription, taskLabel } from './editor-task-binding.js';
export { renderPromptManagement as _renderPromptManagement };

export function _renderProviderList(providers, descs) {
  return renderProviderList(providers, descs);
}

export function _renderTagInput(container, values, onChange) {
  return renderTagInput(container, values, onChange);
}

export function _testProvider(providersObj, providerName) {
  return testProvider(providersObj, providerName);
}

export function _attachProviderListHandlers(pane, providersObj) {
  attachProviderListHandlers(pane, providersObj, renderConfig);
}
export function _renderTaskBinding(tasks, providersObj, descs, opts = {}) {
  return renderTaskBinding(tasks, providersObj, descs, opts, _renderTooltip);
}

export function _attachTaskBindingHandlers(pane, cfg, opts = {}) {
  attachTaskBindingHandlers(pane, cfg, opts, renderConfig);
}
// Dynamic import avoids static cycle: editor.js → editor-config.js → editor.js (A-006)

// Labels and tooltips are rebuilt from the catalogs on every language switch.
onLocaleChange(() => {
  if (state.currentEntity === 'config') renderConfig();
  else if (state.currentEntity === 'tokens') renderTokens();
  else if (state.currentEntity === 'logs') renderLogs();
});

export async function initProjectConfig() {
  try {
    const btn = $('btn-config-init');
    if (btn) { btn.disabled = true; btn.textContent = t('settings.init.creating'); }
    const r = await api('POST', '/api/config/init', {});
    if (r.ok) {
      setStatus(t('settings.init.created'), 'ok');
      const [raw, global, project] = await Promise.all([
        api('GET', '/api/config/raw'),
        api('GET', '/api/config/global'),
        api('GET', '/api/config/project'),
      ]);
      state.configRaw = raw;
      state.configGlobal = global || {};
      state.configProject = project || {};
      state._needsConfigInit = false;
      import('./editor.js').then(m => m.renderActiveTab());
    } else {
      setStatus(t('settings.init.failed', { detail: r.error || t('common.unknown_error') }), 'err');
    }
  } catch (e) {
    setStatus(t('settings.init.failed', { detail: e.message }), 'err');
  } finally {
    const btn = $('btn-config-init');
    if (btn) { btn.disabled = false; btn.textContent = t('settings.init.button'); }
  }
}

function _tabBtn(label, tabKey, active) {
  return `<button class="config-tab-btn${active ? ' active' : ''}" data-config-tab="${tabKey}">${label}</button>`;
}

function _renderFallbackWarn() {
  const link = `<a href="#" id="link-create-project-config" style="text-decoration:underline;color:var(--accent)">${t('settings.fallback_warn_link')}</a>`;
  return `<div class="config-fallback-warn" style="background:var(--warning-bg,#fff3cd);border:1px solid var(--warning-border,#ffc107);border-radius:6px;padding:10px 14px;margin-bottom:12px;display:flex;align-items:flex-start;gap:8px;font-size:var(--text-sm);color:var(--text-primary)">
    <span style="font-size:18px;line-height:1">⚠️</span>
    <span>${t('settings.fallback_warn', { link })}</span>
  </div>`;
}

function _renderEnvEditor() {
  return `<div style="margin-bottom:16px;border-bottom:1px solid var(--border);padding-bottom:12px">
    <button id="btn-env-toggle" class="btn-secondary" style="font-size:var(--text-sm)">${icon('file-text', 14)} ${t('settings.env_edit')}</button>
    <div id="env-editor" style="display:none;margin-top:8px">
      <textarea id="env-textarea" style="width:100%;min-height:160px;font-family:var(--font-mono,monospace);font-size:var(--text-xs,12px);padding:8px;background:var(--bg-surface);color:var(--text-primary);border:1px solid var(--border);border-radius:var(--radius-sm)" spellcheck="false"></textarea>
      <div style="display:flex;gap:8px;margin-top:6px">
        <button id="btn-env-save" class="btn-primary" style="font-size:var(--text-sm)">${icon('check', 14)} ${t('settings.env.save')}</button>
        <span id="env-save-msg" class="muted" style="font-size:var(--text-xs);align-self:center"></span>
      </div>
    </div>
  </div>`;
}

function _attachEnvEditor() {
  const envToggle = $('btn-env-toggle');
  const envEditor = $('env-editor');
  const envTextarea = $('env-textarea');
  const envSave = $('btn-env-save');
  const envMsg = $('env-save-msg');
  let envData = { content: '' };
  if (envToggle && envEditor) {
    envToggle.onclick = async () => {
      const visible = envEditor.style.display !== 'none';
      envEditor.style.display = visible ? 'none' : 'block';
      envToggle.innerHTML = visible ? `${icon('file-text', 14)} ${t('settings.env_edit')}` : `${icon('x', 14)} ${t('settings.env.collapse')}`;
      if (!visible && !envData.content) {
        try {
          envData = await api('GET', '/api/env');
          envTextarea.value = envData.content || '';
        } catch (e) {
          envMsg.textContent = t('settings.env.load_failed');
          void setStatus(t('settings.env.read_failed'), 'err');
        }
      }
    };
  }
  if (envSave && envTextarea && envMsg) {
    envSave.onclick = async () => {
      envMsg.textContent = t('common.saving');
      try {
        const r = await api('PUT', '/api/env', { content: envTextarea.value });
        let message;
        if (r.ok) {
          message = t('settings.env.saved_to', { path: r.path });
          envMsg.textContent = `✓ ${message}`;
          envData.content = envTextarea.value;
        } else {
          message = r.error || t('settings.env.save_failed');
          envMsg.textContent = `✗ ${message}`;
          void setStatus(message, 'err');
          return;
        }
        void setStatus(message, 'ok');
      } catch (e) {
        const message = t('settings.env.save_failed_detail', { detail: e.message || e });
        envMsg.textContent = `✗ ${message}`;
        void setStatus(message, 'err');
      }
    };
  }
}

function _ensureDefaultProviderModels() {
  const providers = state.configGlobal?.ai?.providers;
  if (!providers) return;
  for (const [name, p] of Object.entries(providers)) {
    if (p && (!p.capabilities || p.capabilities.length === 0)) {
      p.capabilities = [...providerCapabilities(p)];
    }
  }
  for (const name of DEFAULT_PROVIDERS) {
    const p = providers[name];
    if (p && (!p.models || p.models.length === 0)) {
      const defaults = DEFAULT_MODELS[name];
      if (defaults) {
        p.models = [...defaults];
      }
    }
  }
}


export function renderConfig() {
  const pane = $('tab-config');
  void _loadConfigSchema();
  if (state._needsConfigInit) {
    pane.innerHTML = `
      <h3>${t('settings.init.title')}</h3>
      <p class="muted">${t('settings.init.body')}</p>
      <p class="hint">${t('settings.init.hint')}</p>
      <button id="btn-config-init" class="btn-primary" style="margin-top:12px">${icon('settings', 16)} ${t('settings.init.button')}</button>
    `;
    const btn = $('btn-config-init');
    if (btn) btn.onclick = initProjectConfig;
    return;
  }
  if (!state.configRaw || Object.keys(state.configRaw).length === 0) {
    pane.innerHTML = `<p class="muted">${t('settings.unavailable')}</p>`;
    return;
  }

  _ensureDefaultProviderModels();

  const isFallback = state.configRaw._config_source === 'global_fallback';
  const active = state.configTab || 'project';
  const descs = state.configRaw._descriptions || {};
  const showAdvanced = _isAdvancedVisible();

  let contentHtml = '';
  if (active === 'project') {
    const projectData = state.configProject || {};
    contentHtml = `<div class="config-form">${_renderConfigProject(projectData, state.configGlobal, descs)}</div>`;
  } else if (active === 'global') {
    state.configGlobal = ensureGlobalUiDefaults(state.configGlobal || {});
    const globalData = state.configGlobal;
    contentHtml = _renderEnvEditor()
      + `<div class="config-form">${_renderConfigGlobal(globalData, descs)}</div>`;
  } else if (active === 'prompts') {
    contentHtml = '<div id="prompt-management-root"></div>';
  } else {
    // Merged tab: read-only merged view
    const { _config_source, _needsConfigInit, _descriptions, ...configData } = state.configRaw;
    contentHtml = `<p class="hint" style="margin-bottom:8px">${t('settings.merged_hint')}</p>
      <div class="config-form config-merged">${_renderConfigForm(configData, '', descs)}</div>`;
  }

  pane.innerHTML = `
    <div class="config-tab-bar">
      ${_tabBtn(t('settings.tabs.project'), 'project', active === 'project')}
      ${_tabBtn(t('settings.tabs.global'), 'global', active === 'global')}
      ${_tabBtn(t('settings.tabs.prompts'), 'prompts', active === 'prompts')}
      ${_tabBtn(t('settings.tabs.merged'), 'merged', active === 'merged')}
      <label class="config-advanced-toggle" style="margin-left:auto;display:flex;align-items:center;gap:6px;font-size:var(--text-sm)">
        <input type="checkbox" id="config-advanced-toggle" ${showAdvanced ? 'checked' : ''}> ${t('settings.show_advanced')}
      </label>
    </div>
    ${isFallback ? _renderFallbackWarn() : ''}
    <div id="config-tab-content">${contentHtml}</div>`;

  pane.querySelectorAll('.browse-btn, [data-desktop-browse]').forEach((btn) => {
    btn.dataset.pickScope = active === 'global' ? 'config' : 'project';
  });
  setBrowseButtonsVisible(pane);

  const createConfigLink = pane.querySelector('#link-create-project-config');
  if (createConfigLink) {
    createConfigLink.onclick = (e) => {
      e.preventDefault();
      initProjectConfig();
    };
  }

  // Tab switching
  pane.querySelectorAll('.config-tab-btn').forEach(btn => {
    btn.onclick = async () => {
      if (btn.dataset.configTab === state.configTab) return;
      if (state.dirty && !(await confirmDialog(t('settings.dirty_switch_confirm')))) return;
      state.configTab = btn.dataset.configTab;
      clearDirty();
      renderConfig();
    };
  });

  const advToggle = pane.querySelector('#config-advanced-toggle');
  if (advToggle) {
    advToggle.onchange = () => {
      _setAdvancedVisible(advToggle.checked);
      renderConfig();
    };
  }

  // Hide save button for merged tab (read-only)
  const saveBtn = $('btn-save');
  if (saveBtn) {
    saveBtn.style.display = active === 'merged' || active === 'prompts' ? 'none' : '';
  }

  // Attach change handlers for the active tab
  if (active === 'global') {
    _attachConfigForm(pane, state.configGlobal || {}, descs);
    _attachEnvEditor();
    _attachProviderListHandlers(pane, state.configGlobal?.ai?.providers || {});
    _attachTaskBindingHandlers(pane, state.configGlobal, { tasksField: 'default_tasks' });
  } else if (active === 'project') {
    _attachConfigForm(pane, state.configProject || {}, descs);
    _attachContextTemplate(pane);
    _attachTaskBindingHandlers(pane, state.configProject, {
      tasksField: 'tasks',
      defaults: state.configGlobal?.ai?.default_tasks || {},
    });
  } else if (active === 'prompts') {
    loadPromptManagement();
  }

  // Model management stays in global tab (system-level)
  if (active === 'global') {
    const wrapper = document.createElement('div');
    wrapper.className = 'config-card';
    wrapper.dataset.sectionKey = 'global.whisper-model';
    wrapper.innerHTML = `
      <div class="config-card-header" tabindex="0" role="button">
        <span class="config-card-arrow">▶</span>
        <span class="config-card-title">${t('settings.local_whisper_models')}</span>
      </div>
      <div class="config-card-body"></div>`;
    wrapper.querySelector('.config-card-body').appendChild(renderModelManagement());
    pane.querySelector('#config-tab-content')?.appendChild(wrapper);
    loadModelManagement();
  }

  // Section card collapse toggle (delegated)
  const contentEl = pane.querySelector('#config-tab-content');
  if (contentEl) {
    contentEl.addEventListener('click', (e) => {
      const header = e.target.closest('.config-card-header');
      if (header) {
        const card = header.closest('.config-card');
        if (card) {
          const isNowCollapsed = card.classList.toggle('collapsed');
          const sectionKey = card.dataset.sectionKey;
          if (sectionKey) {
            if (isNowCollapsed) {
              setSectionCollapsed(sectionKey, true);
            } else {
              setSectionCollapsed(sectionKey, false);
            }
          }
        }
      }
    });
  }
}
