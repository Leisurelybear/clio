/**
 * Prompt override listing, editing, save, and restore controls.
 */
import { api, icon } from './api.js';
import { confirmDialog } from './confirm.js';
import { t } from './i18n.js';
import { state } from './state.js';
import { $, escapeHtml, setStatus } from './utils.js';

const PROMPT_LABEL_KEYS = {
  ANALYZE_PROMPT: 'settings.prompt.label.ANALYZE_PROMPT',
  SCRIPT_PROMPT: 'settings.prompt.label.SCRIPT_PROMPT',
  PLAN_PROMPT: 'settings.prompt.label.PLAN_PROMPT',
  REFINE_TEXT_PROMPT: 'settings.prompt.label.REFINE_TEXT_PROMPT',
  REFINE_TEXT_FIX_PROMPT: 'settings.prompt.label.REFINE_TEXT_FIX_PROMPT',
  REFINE_SCRIPT_PROMPT: 'settings.prompt.label.REFINE_SCRIPT_PROMPT',
  REFINE_SCRIPT_FIX_PROMPT: 'settings.prompt.label.REFINE_SCRIPT_FIX_PROMPT',
};

function promptLabel(name) {
  const key = PROMPT_LABEL_KEYS[name];
  return key ? t(key) : name;
}

function promptSourceLabel(item) {
  if (!item?.has_override) return t('settings.prompt.source.system');
  const source = item.source_path || item.override_path || '';
  return source.includes('templates') ? t('settings.prompt.source.file') : t('settings.prompt.source.project');
}

export function renderPromptManagement(payload, selectedName = null) {
  const prompts = payload?.prompts || [];
  if (!prompts.length) {
    return `<div class="config-empty-state"><p class="muted">${t('settings.prompt.empty')}</p></div>`;
  }
  const selected = prompts.find(p => p.name === selectedName) || prompts[0];
  const listHtml = prompts.map(p => {
    const active = p.name === selected.name ? ' active' : '';
    const badge = p.has_override
      ? `<span class="prompt-badge override">${t('settings.prompt.badge.override')}</span>`
      : `<span class="prompt-badge">${t('settings.prompt.badge.default')}</span>`;
    return `<button class="prompt-list-item${active}" data-prompt-name="${escapeHtml(p.name)}">
      <span class="prompt-list-title">${escapeHtml(promptLabel(p.name))}</span>
      <span class="prompt-list-name">${escapeHtml(p.name)}</span>
      ${badge}
    </button>`;
  }).join('');
  const sourcePath = selected.source_path || selected.override_path || '';
  return `<div class="prompt-management">
    <div class="prompt-list">${listHtml}</div>
    <div class="prompt-editor">
      <div class="prompt-editor-head">
        <div>
          <h4>${escapeHtml(promptLabel(selected.name))}</h4>
          <p class="hint">${escapeHtml(selected.name)} · ${escapeHtml(promptSourceLabel(selected))}</p>
        </div>
        <div class="prompt-actions">
          <button id="btn-prompt-save" class="btn-primary">${icon('save', 14)} ${t('settings.prompt.save_override')}</button>
          <button id="btn-prompt-restore" class="btn-secondary" ${selected.has_override ? '' : 'disabled'}>${icon('refresh', 14)} ${t('settings.prompt.restore_default')}</button>
        </div>
      </div>
      <textarea id="prompt-editor-text" class="prompt-editor-text" spellcheck="false">${escapeHtml(selected.content || '')}</textarea>
      <div class="prompt-meta">
        <span>${t('settings.prompt.save_path', { path: escapeHtml(selected.override_path || '') })}</span>
        ${sourcePath ? `<span>${t('settings.prompt.current_source', { path: escapeHtml(sourcePath) })}</span>` : ''}
      </div>
      <details class="prompt-default-preview">
        <summary>${t('settings.prompt.view_default')}</summary>
        <pre>${escapeHtml(selected.default || '')}</pre>
      </details>
    </div>
  </div>`;
}

function refreshPromptManagement() {
  const container = $('prompt-management-root');
  if (!container) return;
  container.innerHTML = renderPromptManagement(state.promptPayload, state.currentPromptName);
  attachPromptManagementHandlers(container);
}

export async function loadPromptManagement() {
  const container = $('prompt-management-root');
  if (!container) return;
  container.innerHTML = `<p class="muted">${t('settings.prompt.loading')}</p>`;
  try {
    state.promptPayload = await api('GET', '/api/prompts');
    const prompts = state.promptPayload?.prompts || [];
    if (!state.currentPromptName && prompts.length) {
      state.currentPromptName = prompts[0].name;
    }
    refreshPromptManagement();
  } catch (e) {
    const message = t('settings.prompt.load_failed', { detail: e.message || e });
    container.innerHTML = `<p class="err">${escapeHtml(message)}</p>`;
    setStatus(message, 'err');
  }
}

function attachPromptManagementHandlers(container) {
  container.querySelectorAll('.prompt-list-item').forEach(btn => {
    btn.onclick = () => {
      state.currentPromptName = btn.dataset.promptName;
      refreshPromptManagement();
    };
  });
  const selected = (state.promptPayload?.prompts || []).find(p => p.name === state.currentPromptName)
    || state.promptPayload?.prompts?.[0];
  const textarea = container.querySelector('#prompt-editor-text');
  const saveBtn = container.querySelector('#btn-prompt-save');
  const restoreBtn = container.querySelector('#btn-prompt-restore');
  if (saveBtn && selected && textarea) {
    saveBtn.onclick = async () => {
      const content = textarea.value.trim();
      if (!content) {
        setStatus(t('settings.prompt.empty_content'), 'err');
        return;
      }
      saveBtn.disabled = true;
      try {
        await api('PUT', `/api/prompts/${encodeURIComponent(selected.name)}`, { content });
        setStatus(t('settings.prompt.saved'), 'ok');
        state.promptPayload = await api('GET', '/api/prompts');
        refreshPromptManagement();
      } catch (e) {
        setStatus(t('settings.env.save_failed_detail', { detail: e.message || e }), 'err');
      } finally {
        saveBtn.disabled = false;
      }
    };
  }
  if (restoreBtn && selected) {
    restoreBtn.onclick = async () => {
      if (!selected.has_override) return;
      if (!(await confirmDialog(t('settings.prompt.restore_confirm', { name: selected.name })))) return;
      restoreBtn.disabled = true;
      try {
        await api('DELETE', `/api/prompts/${encodeURIComponent(selected.name)}`);
        setStatus(t('settings.prompt.restored'), 'ok');
        state.promptPayload = await api('GET', '/api/prompts');
        refreshPromptManagement();
      } catch (e) {
        setStatus(t('settings.prompt.restore_failed', { detail: e.message || e }), 'err');
      } finally {
        restoreBtn.disabled = false;
      }
    };
  }
}
