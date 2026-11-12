/**
 * AI task binding labels, merged defaults, rendering, and edit handlers.
 */
import { confirmDialog } from './confirm.js';
import { hasKey, t } from './i18n.js';
import { state } from './state.js';
import { clearDirty, escapeHtml, markDirty, setDeep } from './utils.js';
import { providerCapabilities, providerTypeLabel } from './editor-providers.js';

export function taskLabel(taskKey) {
  const key = `settings.task.label.${taskKey}`;
  return hasKey(key) ? t(key) : taskKey;
}

export function taskDescription(taskKey) {
  const key = `settings.task.desc.${taskKey}`;
  return hasKey(key) ? t(key) : '';
}

export function mergeTaskBindings(defaults = {}, overrides = {}) {
  const merged = { ...defaults, ...overrides };
  if (overrides.video_analyze && !overrides.refine_text) {
    merged.refine_text = overrides.video_analyze;
  } else if (merged.video_analyze && !merged.refine_text) {
    merged.refine_text = merged.video_analyze;
  }
  return merged;
}

export function renderTaskBinding(tasks, providersObj, descs, opts = {}, renderTooltip = () => '') {
  const scope = opts.scope || 'project';
  const defaults = opts.defaults || {};
  const overrides = tasks || {};
  const merged = scope === 'project' ? mergeTaskBindings(defaults, overrides) : overrides;
  const providerKeys = Object.keys(providersObj || {});
  let html = `<fieldset class="config-fieldset"><legend>${t('settings.ai_task_binding')}</legend>`;
  if (scope === 'global') {
    html += `<p class="hint" style="margin:0 0 8px;line-height:1.6">${t('settings.task_binding.global_hint')}</p>`;
  } else {
    html += `<p class="hint" style="margin:0 0 8px;line-height:1.6">${t('settings.task_binding.project_hint')}</p>`;
  }
  html += `<p class="hint" style="margin:0 0 8px;line-height:1.6">${t('settings.task_binding.rules_hint')}</p>`;

  if (providerKeys.length === 0) {
    html += `<div class="config-empty-state"><p class="muted">${t('settings.task_binding.empty')}</p><p class="hint">${t('settings.task_binding.empty_hint')}</p><p><a href="#" id="goto-global-providers">${t('settings.task_binding.go_add')}</a></p></div>`;
    html += '</fieldset>';
    return html;
  }

  for (const taskKey of ['video_analyze', 'voiceover', 'vlog_plan', 'refine_text']) {
    const label = taskLabel(taskKey);
    const desc = taskDescription(taskKey);
    const isRefine = taskKey === 'refine_text';
    const hasOverride = Boolean(tasks && tasks[taskKey]);
    const inheritedRefine = scope === 'project'
      && isRefine
      && !hasOverride
      && !overrides.video_analyze
      && Boolean(defaults.refine_text);
    const taskCfg = inheritedRefine ? defaults.refine_text : merged[taskKey];
    const isFollowing = isRefine && !inheritedRefine && !hasOverride;
    const inherited = scope === 'project' && !hasOverride && Boolean(defaults[taskKey]);

    html += '<div class="task-binding-card">';
    html += '<div class="task-binding-header">';
    html += `<span class="task-binding-name">${escapeHtml(label)}${renderTooltip('ai.tasks.' + taskKey, desc)}</span>`;
    if (inherited) html += `<span class="task-binding-scope inherited">${t('settings.task_binding.inherited')}</span>`;
    else if (hasOverride && scope === 'project') {
      html += `<span class="task-binding-scope override">${t('settings.task_binding.override')}</span>`;
      if (defaults[taskKey]) {
        html += `<button type="button" class="task-binding-reset" data-task="${taskKey}">${t('settings.task_binding.reset')}</button>`;
      }
    }
    html += '</div>';

    if (isRefine) {
      html += '<div class="task-binding-row">';
      html += '<label class="task-binding-label refine-follow-cb">';
      html += `<input type="checkbox" class="refine-follow-check" data-task="${taskKey}" ${isFollowing ? 'checked' : ''}> ${t('settings.task_binding.follow')}`;
      html += '</label>';
      html += `<span class="hint" style="font-size:var(--text-xs)">${t('settings.task_binding.follow_hint')}</span>`;
      html += '</div>';
    }

    if (isFollowing) {
      const vaCfg = merged.video_analyze || {};
      html += '<div class="task-binding-row">';
      html += `<span class="muted">${t('settings.task_binding.inherited_from', { provider: escapeHtml(vaCfg.provider || t('settings.task_binding.unset')), model: escapeHtml(vaCfg.model || t('settings.task_binding.unset')) })}</span>`;
      html += '</div>';
    } else {
      const currentProvider = taskCfg?.provider || '';
      const currentModel = taskCfg?.model || '';

      let eligibleProviders = providerKeys;
      if (taskKey === 'video_analyze') {
        eligibleProviders = providerKeys.filter(k => providerCapabilities(providersObj[k]).includes('video'));
      }

      html += '<div class="task-binding-row">';
      html += '<label class="task-binding-label">Provider</label>';
      html += `<select class="task-provider-select" data-task="${taskKey}">`;
      html += `<option value="">${t('settings.provider.select_placeholder')}</option>`;
      for (const pk of eligibleProviders) {
        const p = providersObj[pk];
        const typeLabel = providerTypeLabel(p?.type);
        const selected = pk === currentProvider ? ' selected' : '';
        html += `<option value="${escapeHtml(pk)}"${selected}>${escapeHtml(pk)} (${typeLabel})</option>`;
      }
      html += '</select>';
      if (taskKey === 'video_analyze' && eligibleProviders.length === 0 && providerKeys.length > 0) {
        html += `<span class="warn" style="font-size:var(--text-xs)">${t('settings.provider.need_video_capability')}</span>`;
      }
      html += '</div>';

      const hasProvider = currentProvider && providersObj[currentProvider];
      html += '<div class="task-binding-row">';
      html += `<label class="task-binding-label">${t('settings.task_binding.model_label')}</label>`;
      if (hasProvider) {
        const models = providersObj[currentProvider].models || [];
        if (models.length > 0) {
          html += `<select class="task-model-select" data-task="${taskKey}">`;
          html += `<option value="">${t('settings.provider.select_model_placeholder')}</option>`;
          for (const m of models) {
            const selected = m === currentModel ? ' selected' : '';
            html += `<option value="${escapeHtml(m)}"${selected}>${escapeHtml(m)}</option>`;
          }
          html += '</select>';
        } else {
          html += `<span class="warn">⚠️ ${t('settings.provider.no_models')} <a href="#" class="edit-provider-link" data-provider="${escapeHtml(currentProvider)}" style="color:var(--accent);font-size:var(--text-xs)">${t('common.edit')}</a></span>`;
        }
      } else {
        html += `<span class="muted">${t('settings.provider.select_provider_first')}</span>`;
      }
      html += '</div>';

      const currentTemp = taskCfg?.temperature;
      const tempValue = currentTemp === undefined || currentTemp === null ? '' : currentTemp;
      html += '<div class="task-binding-row">';
      html += `<label class="task-binding-label">${t('settings.task_binding.temperature_label')}</label>`;
      html += `<input type="number" class="form-input task-temperature-input" data-task="${taskKey}" min="0" max="2" step="0.1" value="${escapeHtml(String(tempValue))}" placeholder="${t('settings.task_binding.temperature_placeholder')}">`;
      html += '</div>';
    }

    html += '</div>';
  }

  html += '</fieldset>';
  return html;
}

function _escapeCssAttributeValue(value) {
  return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\A ');
}

export function attachTaskBindingHandlers(pane, cfg, opts = {}, reRender = () => {}) {
  if (!pane) return;

  const tasksField = opts.tasksField || 'tasks';
  const taskBase = `ai.${tasksField}`;

  pane.querySelectorAll('.task-provider-select').forEach(sel => {
    sel.onchange = () => {
      const taskKey = sel.dataset.task;
      setDeep(cfg, `${taskBase}.${taskKey}.provider`, sel.value);
      setDeep(cfg, `${taskBase}.${taskKey}.model`, '');
      markDirty();
      reRender();
    };
  });

  pane.querySelectorAll('.task-model-select').forEach(sel => {
    sel.onchange = () => {
      const taskKey = sel.dataset.task;
      setDeep(cfg, `${taskBase}.${taskKey}.model`, sel.value);
      markDirty();
    };
  });

  pane.querySelectorAll('.task-temperature-input').forEach(inp => {
    inp.onchange = () => {
      const taskKey = inp.dataset.task;
      const raw = inp.value.trim();
      const inherited = (opts.defaults || {})[taskKey] || {};
      const existing = cfg.ai?.[tasksField]?.[taskKey] || {};
      const taskCfg = { ...inherited, ...existing };
      if (!raw) {
        delete taskCfg.temperature;
      } else {
        const num = Number(raw);
        if (!Number.isFinite(num) || num < 0 || num > 2) {
          inp.value = existing.temperature ?? '';
          return;
        }
        taskCfg.temperature = num;
      }
      if (!cfg.ai) cfg.ai = {};
      if (!cfg.ai[tasksField]) cfg.ai[tasksField] = {};
      cfg.ai[tasksField][taskKey] = taskCfg;
      markDirty();
    };
  });

  pane.querySelectorAll('.refine-follow-check').forEach(cb => {
    cb.onchange = () => {
      if (!cfg.ai) cfg.ai = {};
      if (!cfg.ai[tasksField]) cfg.ai[tasksField] = {};
      if (cb.checked) {
        delete cfg.ai[tasksField].refine_text;
      } else {
        cfg.ai[tasksField].refine_text = { provider: '', model: '' };
      }
      markDirty();
      reRender();
    };
  });

  pane.querySelectorAll('.task-binding-reset').forEach(btn => {
    btn.onclick = () => {
      const taskKey = btn.dataset.task;
      if (cfg.ai?.[tasksField]) delete cfg.ai[tasksField][taskKey];
      markDirty();
      reRender();
    };
  });

  const gotoBtn = pane.querySelector('#goto-global-providers');
  if (gotoBtn) {
    gotoBtn.onclick = async (e) => {
      e.preventDefault();
      if (state.dirty && !(await confirmDialog(t('settings.dirty_switch_confirm')))) return;
      state.configTab = 'global';
      clearDirty();
      reRender();
    };
  }

  pane.querySelectorAll('.edit-provider-link').forEach(link => {
    link.onclick = async (e) => {
      e.preventDefault();
      if (state.dirty && !(await confirmDialog(t('settings.dirty_switch_confirm')))) return;
      const pName = link.dataset.provider;
      state.configTab = 'global';
      clearDirty();
      reRender();
      setTimeout(() => {
        const editBtn = document.querySelector(`.btn-provider-edit[data-provider="${_escapeCssAttributeValue(pName)}"]`);
        if (editBtn) editBtn.click();
      }, 100);
    };
  });
}
