import { state } from './state.js';
import { $, $$, escapeHtml, markDirty, clearDirty, setStatus, setDeep } from './utils.js';
import { api, icon } from './api.js';
import { setBrowseButtonsVisible } from './desktop-pick.js';
import {
  DEFAULT_MODELS,
  attachProviderListHandlers,
  providerCapabilities,
  providerEnvName,
  providerTypeLabel,
  renderProviderList,
  renderTagInput,
  testProvider,
} from './editor-providers.js';
import { renderLogs, stopLogsPolling } from './editor-logs.js';
import { loadModelManagement, renderModelManagement } from './editor-models.js';
import { renderTokens } from './editor-tokens.js';
import { hasKey, onLocaleChange, t } from './i18n.js';
import { confirmDialog } from './confirm.js';
import { ensureGlobalUiDefaults } from './language-switcher.js';

export { renderLogs, renderTokens, stopLogsPolling };
export { providerEnvName, providerTypeLabel };

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
// Dynamic import avoids static cycle: editor.js → editor-config.js → editor.js (A-006)

const ADVANCED_TOGGLE_KEY = 'vlog-config-show-advanced';
let _configSchema = null;

// Labels and tooltips are rebuilt from the catalogs on every language switch.
onLocaleChange(() => {
  if (state.currentEntity === 'config') renderConfig();
  else if (state.currentEntity === 'tokens') renderTokens();
  else if (state.currentEntity === 'logs') renderLogs();
});

export function _isAdvancedVisible() {
  return localStorage.getItem(ADVANCED_TOGGLE_KEY) === 'true';
}

export function _setAdvancedVisible(v) {
  localStorage.setItem(ADVANCED_TOGGLE_KEY, v ? 'true' : 'false');
}

function _schemaField(path, layer = 'project') {
  if (!_configSchema || !_configSchema[layer]) return null;
  for (const sec of _configSchema[layer]) {
    for (const f of sec.fields || []) {
      if (f.path === path) return f;
    }
  }
  return null;
}

export async function _loadConfigSchema() {
  if (_configSchema) return _configSchema;
  try {
    _configSchema = await api('GET', '/api/config/schema');
  } catch { _configSchema = null; }
  return _configSchema;
}
const PROMPT_LABEL_KEYS = {
  ANALYZE_PROMPT: 'settings.prompt.label.ANALYZE_PROMPT',
  SCRIPT_PROMPT: 'settings.prompt.label.SCRIPT_PROMPT',
  PLAN_PROMPT: 'settings.prompt.label.PLAN_PROMPT',
  REFINE_TEXT_PROMPT: 'settings.prompt.label.REFINE_TEXT_PROMPT',
  REFINE_TEXT_FIX_PROMPT: 'settings.prompt.label.REFINE_TEXT_FIX_PROMPT',
  REFINE_SCRIPT_PROMPT: 'settings.prompt.label.REFINE_SCRIPT_PROMPT',
  REFINE_SCRIPT_FIX_PROMPT: 'settings.prompt.label.REFINE_SCRIPT_FIX_PROMPT',
};


export const FIELD_LABELS = {
  // plan
  'plan.plans_subdir': '规划子目录',
  'plan.max_clips_per_day': '每日最多片段数',
  'plan.target_duration_sec': '目标时长（秒）',
  'plan.use_transcripts': '注入语音转录',
  // analyze
  'analyze.compressed_subdir': '压缩视频子目录',
  'analyze.texts_subdir': '分析结果子目录',
  'analyze.skip_existing': '跳过已处理文件',
  'analyze.max_analyze_duration_min': '整片分析硬顶（分钟，0=不限制）',
  'analyze.window_max_min': '分析窗长（分钟）',
  'analyze.window_overlap_sec': '分析窗重叠（秒）',
  'analyze.max_workers': 'AI 分析并发数',
  // script
  'script.scripts_subdir': '口播子目录',
  'script.template_file': '口播模板文件',
  'script.target_words': '口播目标字数',
  // compress (project)
  'compress.target_size_mb': '目标体积（MB）',
  'compress.max_width': '最大宽度（像素）',
  // whisper (project)
  'whisper.enabled': '启用 Whisper',
  'whisper.model_size': '模型大小',
  'whisper.language': '识别语言',
  'whisper.device': '运行设备',
  'whisper.max_segments_per_clip': '每片段最多转录段数',
  'whisper.transcripts_subdir': '转录子目录',
  'whisper.engine': 'ASR 引擎',
  // export
  'export.canvas_ratio': '画布比例',
  'export.output_subdir': '导出子目录',
  'export.jianying_draft_dir': '剪映草稿目录',
  'export.auto_copy_draft': '自动复制草稿',
  // paths
  'paths.output_dir': '输出目录',
  'paths.ffmpeg': 'ffmpeg 路径',
  'paths.ffprobe': 'ffprobe 路径',
  'paths.logs_dir': '日志目录',
  // ai
  'ai.context': '项目背景（追加到 trip_context）',
  'ai.debug_print_prompt': '调试打印完整 prompt',
  'ai.provider_ttl_min': 'Provider 缓存 TTL（分钟）',
  // ui
  'ui.language': '界面语言',
  'ui.desktop_notifications': '后台系统通知',
  // provider
  'max_tokens': 'max_tokens（0=不限制）',
  'timeout_sec': 'HTTP 超时（秒）',
  'retry_attempts': '额外重试次数',
  'requests_per_minute': '每分钟请求上限（0=不限）',
  'poll_interval_sec': '轮询间隔（秒）',
};


export function labelFromPath(path) {
  if (!path) return 'config';
  const key = `settings.field.${path}`;
  if (hasKey(key)) return t(key);
  const leaf = path.split('.').pop();
  const leafKey = `settings.field.${leaf}`;
  if (hasKey(leafKey)) return t(leafKey);
  return FIELD_LABELS[path] || FIELD_LABELS[leaf] || leaf;
}

export function pathPickKind(path) {
  if (path === 'paths.ffmpeg' || path === 'paths.ffprobe') return 'exe';
  if (path === 'script.template_file') return 'any';
  if (
    path === 'paths.output_dir' ||
    path === 'paths.logs_dir' ||
    path === 'whisper.cache_dir' ||
    path === 'export.jianying_draft_dir'
  ) return 'folder';
  return null;
}

function _resolveDescPath(path, descriptions) {
  if (!descriptions) return null;
  if (descriptions[path]) return path;
  const m = path.match(/^(ai\.(?:providers|tasks))\.([^.]+)\.(.+)$/);
  if (m) {
    const candidate = `${m[1]}.{name}.${m[3]}`;
    if (descriptions[candidate]) return candidate;
  }
  return null;
}


/** Localized tooltip text: catalog first, backend-provided Chinese as fallback. */
export function descFromPath(path, descriptions) {
  const resolved = _resolveDescPath(path, descriptions);
  if (!resolved) return '';
  const key = `settings.desc.${resolved}`;
  return hasKey(key) ? t(key) : descriptions[resolved] || '';
}


/** Localized label for a schema `<select>` option; falls back to the raw value. */
export function optionLabel(path, value) {
  const raw = String(value);
  const key = path === 'ui.language' ? `settings.language.${raw}` : `settings.option.${path}.${raw}`;
  return hasKey(key) ? t(key) : raw;
}


export function _renderTooltip(path, desc) {
  if (!desc) return '';
  return `<span class="config-desc-icon" data-desc-path="${path}" tabindex="0">
    <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><circle cx="8" cy="8" r="7"/><text x="8" y="10" text-anchor="middle" font-size="9" fill="white" font-weight="bold">?</text></svg>
    <span class="config-desc-tooltip">${escapeHtml(desc)}</span>
  </span>`;
}


export function _renderConfigForm(obj, path, descriptions = null, layer = 'project') {
  if (obj === null || obj === undefined) {
    return `<span class="config-null">${t('settings.empty_value')}</span>`;
  }
  const tip = _renderTooltip(path, descFromPath(path, descriptions));
  const sf = _schemaField(path, layer);
  if (sf && sf.group === 'hidden') return '';
  if (sf && sf.group === 'advanced' && !_isAdvancedVisible()) return '';
  const advBadge = sf && sf.group === 'advanced' && _isAdvancedVisible()
    ? `<span class="config-adv-badge" title="${t('settings.adv_title')}">ADV</span>` : '';
  if (typeof obj === 'boolean') {
    return `${advBadge}<label class="config-field config-bool"><span class="config-key">${labelFromPath(path)}${tip}</span> <input type="checkbox" data-path="${path}" ${obj ? 'checked' : ''}></label>`;
  }
  if (typeof obj === 'number') {
    if (sf && sf.ui === 'select_or_number' && Array.isArray(sf.choices)) {
      const isPreset = sf.choices.includes(obj);
      const opts = sf.choices.map(c => `<option value="${c}"${c === obj ? ' selected' : ''}>${c}</option>`).join('');
      const customInput = isPreset ? '' : `<input type="number" class="config-custom-num" data-path="${path}" step="any" value="${obj}" style="width:80px;margin-left:4px">`;
      const customOpt = `<option value="__custom__"${isPreset ? '' : ' selected'}>${t('settings.custom_option')}</option>`;
      return `<label class="config-field config-num"><span class="config-key">${labelFromPath(path)}${tip}</span> <select data-path="${path}" data-schema-select="1">${opts}${customOpt}</select>${customInput}</label>`;
    }
    const isInt = Number.isInteger(obj);
    return `${advBadge}<label class="config-field config-num"><span class="config-key">${labelFromPath(path)}${tip}</span> <input type="number" data-path="${path}" step="${isInt ? '1' : 'any'}" value="${obj}"></label>`;
  }
  if (typeof obj === 'string') {
    if (sf && sf.ui === 'select' && Array.isArray(sf.choices)) {
      const opts = sf.choices.map(c => {
        const value = String(c);
        const label = optionLabel(path, value);
        return `<option value="${escapeHtml(value)}"${value === String(obj) ? ' selected' : ''}>${escapeHtml(label)}</option>`;
      }).join('');
      let selectHint = '';
      if (path === 'whisper.engine' && obj && obj !== 'local') {
        selectHint = `<br><span class="hint">${t('settings.hint.whisper_engine')}</span>`;
      }
      return `<label class="config-field config-str"><span class="config-key">${labelFromPath(path)}${tip}</span> <select data-path="${path}"><option value=""${!sf.choices.includes(obj) ? ' selected' : ''}>${t('settings.select_placeholder')}</option>${opts}</select></label>${selectHint}`;
    }
    const multiline = path === 'ai.context' || obj.length > 80 || obj.includes('\n');
    if (multiline) {
      let hint = '';
      if (path === 'ai.context') {
        hint = `<br><span class="hint">${t('settings.hint.ai_context')}</span>`;
      }
      return `<label class="config-field config-str"><span class="config-key">${labelFromPath(path)}${tip}</span> <textarea data-path="${path}" rows="4">${escapeHtml(obj)}</textarea>${hint}</label>`;
    }
    const isPwd = path.endsWith('api_key');
    let hint = '';
    if (path.endsWith('api_key_env')) {
      hint = `<br><span class="hint">${t('settings.hint.api_key_env')}</span>`;
    } else if (path.endsWith('base_url')) {
      hint = `<br><span class="hint">${t('settings.hint.base_url')}</span>`;
    } else if (path.endsWith('api_key')) {
      hint = `<br><span class="hint">${t('settings.hint.api_key')}</span>`;
    }
    const pickKind = pathPickKind(path);
    const browseBtn = pickKind
      ? `<button type="button" class="browse-btn" data-desktop-browse data-pick-kind="${pickKind}" data-target-path="${path}">${t('settings.browse')}</button>`
      : '';
    const fieldInput = browseBtn
      ? `<span class="input-with-browse"><input type="${isPwd ? 'password' : 'text'}" data-path="${path}" value="${escapeHtml(obj)}">${browseBtn}</span>`
      : `<input type="${isPwd ? 'password' : 'text'}" data-path="${path}" value="${escapeHtml(obj)}">`;
    return `<label class="config-field config-str"><span class="config-key">${labelFromPath(path)}${tip}</span> ${fieldInput}</label>${hint}`;
  }
  if (Array.isArray(obj)) {
    const allStr = obj.every(x => typeof x === 'string');
    if (allStr) {
      return `<fieldset class="config-fieldset"><legend>${labelFromPath(path)}</legend><label class="config-field config-str"><textarea data-path="${path}" rows="${Math.max(2, obj.length)}">${escapeHtml(obj.join('\n'))}</textarea><span class="hint">${t('settings.one_per_line')}</span></label></fieldset>`;
    }
    return `<fieldset class="config-fieldset"><legend>${labelFromPath(path)}</legend>${obj.map((item, i) =>
      `<div class="config-array-item">${_renderConfigForm(item, path + '[' + i + ']', descriptions)}</div>`
    ).join('')}</fieldset>`;
  }
  if (typeof obj === 'object') {
    let html = `<fieldset class="config-fieldset"><legend>${labelFromPath(path) || t('settings.section_generic')}${tip}</legend>`;
    for (const [key, val] of Object.entries(obj)) {
      if (key === 'context_file') continue;
      html += _renderConfigForm(val, path ? `${path}.${key}` : key, descriptions);
    }
    html += '</fieldset>';
    return html;
  }
  return `<span class="muted">${escapeHtml(String(obj))}</span>`;
}


const _collapsedCards = new Map(); // key -> boolean (explicit state) | undefined (unset)

function _sectionLabel(key) {
  const i18nKey = `settings.section.${key}`;
  return hasKey(i18nKey) ? t(i18nKey) : key;
}

function _isSectionCollapsed(key, isGlobal) {
  if (isGlobal) {
    return ['proxy', 'compress', 'whisper'].includes(key);
  }
  return ['analyze', 'script', 'whisper', 'export'].includes(key);
}

function _renderSectionCard(key, val, descs, isGlobal) {
  const label = _sectionLabel(key);
  const sectionKey = (isGlobal ? 'global.' : 'project.') + key;
  const wasCollapsed = _collapsedCards.get(sectionKey);
  const collapsed = wasCollapsed !== undefined ? wasCollapsed : _isSectionCollapsed(key, isGlobal);
  let fieldsHtml = '';
  if (typeof val === 'object' && val !== null && !Array.isArray(val)) {
    for (const [k, v] of Object.entries(val)) {
      fieldsHtml += _renderConfigForm(v, `${key}.${k}`, descs, isGlobal ? 'global' : 'project');
    }
  } else {
    fieldsHtml = _renderConfigForm(val, key, descs, isGlobal ? 'global' : 'project');
  }
  if (!fieldsHtml.trim()) return '';
  return `<div class="config-card${collapsed ? ' collapsed' : ''}" data-section-key="${sectionKey}">
    <div class="config-card-header" tabindex="0" role="button">
      <span class="config-card-arrow">▶</span>
      <span class="config-card-title">${label}</span>
    </div>
    <div class="config-card-body">${fieldsHtml}</div>
  </div>`;
}


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


export function _renderConfigGlobal(globalData, descs) {
  const order = ['paths', 'server', 'naming', 'proxy', 'ui', 'compress', 'whisper'];
  let html = '';
  for (const key of order) {
    if (key in globalData) {
      html += _renderSectionCard(key, globalData[key], descs, true);
    }
  }
  const aiCfg = globalData.ai || {};
  const { providers, default_tasks, ...otherAiFields } = aiCfg;
  if (Object.keys(otherAiFields).length > 0) {
    html += _renderSectionCard('ai', otherAiFields, descs, true);
  }
  html += `<div class="config-card" data-section-key="global.task-binding">
    <div class="config-card-header" tabindex="0" role="button">
      <span class="config-card-arrow">▶</span>
      <span class="config-card-title">${t('settings.ai_default_task_binding')}</span>
    </div>
    <div class="config-card-body">${_renderTaskBinding(
      default_tasks || {},
      providers || {},
      descs || {},
      { scope: 'global' },
    )}</div>
  </div>`;
  html += `<div class="config-card" data-section-key="global.providers">
    <div class="config-card-header" tabindex="0" role="button">
      <span class="config-card-arrow">▶</span>
      <span class="config-card-title">${t('settings.ai_model_list')}</span>
    </div>
    <div class="config-card-body">${_renderProviderList(providers || {}, descs)}</div>
  </div>`;
  return html;
}

function _renderConfigProject(projectData, globalData, descs) {
  const order = ['paths', 'compress', 'analyze', 'script', 'plan', 'whisper', 'export'];
  let html = '';
  for (const key of order) {
    if (key in projectData) {
      html += _renderSectionCard(key, projectData[key], descs, false);
    }
  }
  const aiCfg = projectData.ai || {};
  const { tasks, ...aiNonTaskFields } = aiCfg;
  if (Object.keys(aiNonTaskFields).length > 0) {
    html += _renderSectionCard('ai', aiNonTaskFields, descs, false);
  }
  html += `<div class="config-card" data-section-key="project.task-binding">
    <div class="config-card-header" tabindex="0" role="button">
      <span class="config-card-arrow">▶</span>
      <span class="config-card-title">${t('settings.ai_task_binding')}</span>
    </div>
    <div class="config-card-body">${_renderTaskBinding(
      tasks || {},
      globalData?.ai?.providers || {},
      descs || {},
      { scope: 'project', defaults: globalData?.ai?.default_tasks || {} },
    )}</div>
  </div>`;
  return html;
}

function _promptLabel(name) {
  const key = PROMPT_LABEL_KEYS[name];
  return key ? t(key) : name;
}

function _promptSourceLabel(item) {
  if (!item?.has_override) return t('settings.prompt.source.system');
  const source = item.source_path || item.override_path || '';
  return source.includes('templates') ? t('settings.prompt.source.file') : t('settings.prompt.source.project');
}

export function _renderPromptManagement(payload, selectedName = null) {
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
      <span class="prompt-list-title">${escapeHtml(_promptLabel(p.name))}</span>
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
          <h4>${escapeHtml(_promptLabel(selected.name))}</h4>
          <p class="hint">${escapeHtml(selected.name)} · ${escapeHtml(_promptSourceLabel(selected))}</p>
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

async function _loadPromptManagement() {
  const container = $('prompt-management-root');
  if (!container) return;
  container.innerHTML = `<p class="muted">${t('settings.prompt.loading')}</p>`;
  try {
    state.promptPayload = await api('GET', '/api/prompts');
    const prompts = state.promptPayload?.prompts || [];
    if (!state.currentPromptName && prompts.length) {
      state.currentPromptName = prompts[0].name;
    }
    _refreshPromptManagement();
  } catch (e) {
    const message = t('settings.prompt.load_failed', { detail: e.message || e });
    container.innerHTML = `<p class="err">${escapeHtml(message)}</p>`;
    setStatus(message, 'err');
  }
}

function _refreshPromptManagement() {
  const container = $('prompt-management-root');
  if (!container) return;
  container.innerHTML = _renderPromptManagement(state.promptPayload, state.currentPromptName);
  _attachPromptManagementHandlers(container);
}

function _attachPromptManagementHandlers(container) {
  container.querySelectorAll('.prompt-list-item').forEach(btn => {
    btn.onclick = () => {
      state.currentPromptName = btn.dataset.promptName;
      _refreshPromptManagement();
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
        _refreshPromptManagement();
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
        _refreshPromptManagement();
      } catch (e) {
        setStatus(t('settings.prompt.restore_failed', { detail: e.message || e }), 'err');
      } finally {
        restoreBtn.disabled = false;
      }
    };
  }
}

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

export function _renderTaskBinding(tasks, providersObj, descs, opts = {}) {
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
    html += `<span class="task-binding-name">${escapeHtml(label)}${_renderTooltip('ai.tasks.' + taskKey, desc)}</span>`;
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

function _attachTaskBindingHandlers(pane, cfg, opts = {}) {
  if (!pane) return;
  const reRender = () => renderConfig();
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
      renderConfig();
    };
  }

  pane.querySelectorAll('.edit-provider-link').forEach(link => {
    link.onclick = async (e) => {
      e.preventDefault();
      if (state.dirty && !(await confirmDialog(t('settings.dirty_switch_confirm')))) return;
      const pName = link.dataset.provider;
      state.configTab = 'global';
      clearDirty();
      renderConfig();
      setTimeout(() => {
        const editBtn = document.querySelector(`.btn-provider-edit[data-provider="${_escapeCssAttributeValue(pName)}"]`);
        if (editBtn) editBtn.click();
      }, 100);
    };
  });
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

function _numFromFieldValue(raw) {
  if (raw === '') return '';
  const n = raw.includes('.') ? parseFloat(raw) : parseInt(raw, 10);
  return Number.isNaN(n) ? raw : n;
}

export function _attachConfigForm(pane, sourceObj, descriptions) {
  const bind = (el) => {
    const onchange = () => {
      if (el.tagName === 'SELECT' && el.dataset.schemaSelect !== undefined) {
        if (el.value === '__custom__') {
          focusCustomNumber(el);
          return;
        }
        const custom = el.parentNode.querySelector('.config-custom-num');
        if (custom) custom.remove();
        setDeep(sourceObj, el.dataset.path, _numFromFieldValue(el.value));
        markDirty();
        return;
      }
      let val;
      if (el.type === 'checkbox') {
        val = el.checked;
      } else if (el.type === 'number') {
        val = _numFromFieldValue(el.value);
      } else {
        val = el.value;
      }
      setDeep(sourceObj, el.dataset.path, val);
      markDirty();
    };
    el.onchange = onchange;
    if (el.tagName === 'INPUT' && el.type === 'text') {
      el.oninput = onchange;
    }
    if (el.tagName === 'TEXTAREA') {
      el.oninput = onchange;
    }
  };

  const focusCustomNumber = (selectEl) => {
    let input = selectEl.parentNode.querySelector('.config-custom-num');
    if (!input) {
      input = document.createElement('input');
      input.type = 'number';
      input.className = 'config-custom-num';
      input.dataset.path = selectEl.dataset.path;
      input.step = 'any';
      input.style.cssText = 'width:80px;margin-left:4px';
      selectEl.after(input);
      bind(input);
    }
    input.focus();
  };

  pane.querySelectorAll('[data-path]').forEach(bind);
  pane.querySelectorAll('.config-desc-icon').forEach(el => {
    el.onclick = (e) => {
      e.stopPropagation();
      el.classList.toggle('show');
    };
  });
}

function _attachContextTemplate(pane) {
  const ctxTextarea = pane.querySelector('textarea[data-path="ai.context"]');
  if (ctxTextarea && !ctxTextarea.value.trim()) {
    const btn = document.createElement('button');
    btn.className = 'btn-primary';
    btn.style.cssText = 'margin-top:6px;font-size:var(--text-sm);padding:5px 12px;';
    btn.innerHTML = `${icon('plus', 14)} ${t('settings.ai_context.add_template')}`;
    btn.onclick = () => {
      const template = t('settings.ai_context.template');
      ctxTextarea.value = template;
      setDeep(state.configProject, 'ai.context', template);
      markDirty();
      btn.remove();
    };
    ctxTextarea.parentNode.appendChild(btn);
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
    _loadPromptManagement();
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
              _collapsedCards.set(sectionKey, true);
            } else {
              _collapsedCards.set(sectionKey, false);
            }
          }
        }
      }
    });
  }
}
