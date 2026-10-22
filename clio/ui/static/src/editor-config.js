import { state } from './state.js';
import { $, $$, escapeHtml, markDirty, clearDirty, setStatus, setDeep } from './utils.js';
import { api, icon } from './api.js';
import { subscribeTaskEvents, fetchTask } from './task-center.js';
import { setBrowseButtonsVisible } from './desktop-pick.js';
import {
  filterLogEntries,
  entryMatchesLogFilter,
  inferLogLevel,
  logLineClass,
  normalizeLogEntry,
  formatLogTime,
} from './logs-filter.js';
import { LOGS_BUFFER_MAX, appendLogEntries } from './logs-buffer.js';
import {
  formatLogHour,
  historyStatusText,
  logFileLabel,
  sortLogFiles,
} from './logs-history.js';
import { transcriptionModelManagementNotice } from './transcription-status.js';
import { hasKey, onLocaleChange, t } from './i18n.js';
import { ensureGlobalUiDefaults } from './language-switcher.js';
// Dynamic import avoids static cycle: editor.js → editor-config.js → editor.js (A-006)

const DEFAULT_PROVIDERS = ['gemini', 'openai', 'deepseek'];
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

const ADVANCED_TOGGLE_KEY = 'vlog-config-show-advanced';
let _configSchema = null;

// Labels and tooltips are rebuilt from the catalogs on every language switch.
onLocaleChange(() => {
  if (state.currentEntity === 'config') renderConfig();
  else if (state.currentEntity === 'tokens') renderTokens();
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

async function _loadConfigSchema() {
  if (_configSchema) return _configSchema;
  try {
    _configSchema = await api('GET', '/api/config/schema');
  } catch { _configSchema = null; }
  return _configSchema;
}
const DEFAULT_MODELS = {
  gemini: ['gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-3-flash', 'gemini-3.1-flash-lite', 'gemini-3.5-flash'],
  openai: ['gpt-4o', 'gpt-4o-mini'],
  deepseek: ['deepseek-chat', 'deepseek-reasoner', 'deepseek-v4-flash', 'deepseek-v4-pro'],
};
const DEFAULT_CAPABILITIES = {
  gemini: ['video', 'text'],
  openai: ['text'],
};

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

function providerCapabilities(provider) {
  if (provider?.capabilities?.length) return provider.capabilities;
  return DEFAULT_CAPABILITIES[provider?.type] || ['text'];
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


export function _renderTagInput(container, values, onChange) {
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
      const customOpt = `<option value="__custom__" selected>${t('settings.custom_option')}</option>`;
      return `<label class="config-field config-num"><span class="config-key">${labelFromPath(path)}${tip}</span> <select data-path="${path}" data-schema-select="1">${isPreset ? opts : opts + customOpt}</select>${customInput}</label>`;
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
      if (!confirm(t('settings.prompt.restore_confirm', { name: selected.name }))) return;
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
        const typeLabel = p?.type === 'gemini' ? t('settings.provider.type.gemini') : t('settings.provider.type.openai_compat');
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
    }

    html += '</div>';
  }

  html += '</fieldset>';
  return html;
}

export function _renderProviderList(providers, descs) {
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
    const typeLabel = p.type === 'gemini' ? t('settings.provider.type.gemini') : t('settings.provider.type.openai_compat');
    const hasModels = p.models && p.models.length > 0;
    const modelTags = hasModels
      ? p.models.map(m => `<span class="tag-chip">${escapeHtml(m)}</span>`).join(' ')
      : `<span class="warn">⚠️ ${t('settings.provider.no_models_badge')}</span>`;
    const capabilityTags = providerCapabilities(p)
      .map(c => `<span class="tag-chip">${escapeHtml(c)}</span>`)
      .join(' ');

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
        <div class="provider-card-field"><span class="provider-card-label">timeout</span><span>${escapeHtml(String(p.timeout_sec ?? 120))}s</span></div>
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

  const typeOptions = ['gemini', 'openai'].map(type =>
    `<option value="${type}"${existing?.type === type ? ' selected' : ''}>${type === 'gemini' ? t('settings.provider.type.gemini_long') : t('settings.provider.type.openai_compat_long')}</option>`
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

    if (!newName) { setStatus(t('settings.provider.name_required'), 'warn'); return; }
    if (isEdit && newName !== name) { setStatus(t('settings.provider.name_immutable'), 'warn'); return; }
    if (!isEdit && providersObj[newName]) { setStatus(t('settings.provider.name_exists', { name: newName }), 'warn'); return; }
    if (newMaxTokens < 0 || newTimeout < 0 || newRetry < 0 || newRpm < 0 || newPoll < 0) {
      setStatus(t('settings.provider.negative_numbers'), 'warn');
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

  const selected = prompt(`${t('settings.provider.test_pick_model')}\n${models.join('\n')}`, models[0]);
  if (selected === null) return { ok: false, error: t('settings.provider.test_canceled'), canceled: true };
  const model = selected.trim();
  if (!models.includes(model)) return { ok: false, error: t('settings.provider.test_invalid_model') };
  return { ok: true, model };
}

export async function _testProvider(providersObj, providerName) {
  const provider = providersObj?.[providerName];
  const choice = _chooseProviderTestModel(provider);
  if (!choice.ok) return choice;

  return api('POST', '/api/ai/test', {
    provider: providerName,
    model: choice.model,
  });
}

export function _attachProviderListHandlers(pane, providersObj) {
  const reRender = () => renderConfig();

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
        const result = await _testProvider(providersObj, providerName);
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
        if (!confirm(t('settings.provider.delete_in_use', { name, refs: refs.join(t('common.list_separator')) }))) return;
      } else {
        if (!confirm(t('settings.provider.delete_confirm', { name }))) return;
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
    btn.onclick = async () => {
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
        if (!value.textContent) {
          const providerName = wrap.dataset.provider;
          const envName = providerName.toUpperCase() + '_API_KEY';
          try {
            const existing = await api('GET', '/api/env');
            const lines = (existing.content || '').split('\n');
            let found = false;
            for (const line of lines) {
              if (line.startsWith(envName + '=')) {
                value.textContent = line.slice(envName.length + 1);
                found = true;
                break;
              }
            }
            if (!found) { setStatus(t('settings.provider.env_not_found', { name: envName }), 'warn'); return; }
          } catch {
            setStatus(t('settings.env.read_failed'), 'err'); return;
          }
        }
        value.style.display = '';
        masked.style.display = 'none';
        btn.textContent = t('settings.provider.hide_key');
      }
    };
  });
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
    gotoBtn.onclick = (e) => {
      e.preventDefault();
      if (state.dirty && !confirm(t('settings.dirty_switch_confirm'))) return;
      state.configTab = 'global';
      clearDirty();
      renderConfig();
    };
  }

  pane.querySelectorAll('.edit-provider-link').forEach(link => {
    link.onclick = (e) => {
      e.preventDefault();
      if (state.dirty && !confirm(t('settings.dirty_switch_confirm'))) return;
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

function _attachConfigForm(pane, sourceObj, descriptions) {
  pane.querySelectorAll('[data-path]').forEach(el => {
    const onchange = () => {
      let val;
      if (el.type === 'checkbox') {
        val = el.checked;
      } else if (el.type === 'number') {
        val = el.value.includes('.') ? parseFloat(el.value) : (el.value === '' ? '' : parseInt(el.value, 10));
        if (isNaN(val)) val = el.value;
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
  });
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
    btn.onclick = () => {
      if (btn.dataset.configTab === state.configTab) return;
      if (state.dirty && !confirm(t('settings.dirty_switch_confirm'))) return;
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
    _loadModelMgmt();
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





let _logsTimer = null;
let _logsOffset = 0;
let _logsAutoScroll = true;
/** @type {Array<{ts: number|null, text: string}>} */
let _logsBuffer = [];
/** Persist across re-open of the logs panel (prefer keep filters after clear). */
let _logsFilterQuery = '';
/** @type {''|'all'|'info'|'warn'|'error'} */
let _logsFilterLevel = 'all';
/** 0 = all; else window in ms */
let _logsFilterSinceMs = 0;
/** Poll generation currently in flight; prevents overlap without blocking a new mode. */
let _logsPollInFlightGen = null;
/** Invalidate in-flight polls when leaving/re-entering live mode. */
let _logsPollGeneration = 0;
/** Empty means live session; otherwise an hourly log basename. */
let _logsSource = '';
/** @type {Array<{ts: number|null, text: string}>} */
let _logsHistoryBuffer = [];
let _logsHistoryTruncated = false;
/** Ignore stale historical-file responses after switching sources. */
let _logsHistorySeq = 0;

const _LOG_LEVELS = ['debug', 'info', 'warn', 'error'];

function _levelLabel(level) {
  return _LOG_LEVELS.includes(level) ? t(`logs.level.${level}`) : t('logs.level.info');
}

const _SINCE_CHIPS = [
  { ms: 0, key: 'logs.since.all' },
  { ms: 5 * 60 * 1000, key: 'logs.since.5m' },
  { ms: 15 * 60 * 1000, key: 'logs.since.15m' },
  { ms: 60 * 60 * 1000, key: 'logs.since.60m' },
];

function _logsFilterOpts() {
  return {
    query: _logsFilterQuery,
    level: _logsFilterLevel,
    sinceMs: _logsFilterSinceMs || undefined,
  };
}

function _activeLogsBuffer() {
  return _logsSource ? _logsHistoryBuffer : _logsBuffer;
}

function _appendLogLineEl(view, entryOrLine) {
  const entry = normalizeLogEntry(entryOrLine);
  const level = inferLogLevel(entry.text);
  const d = document.createElement('div');
  d.className = `log-line ${logLineClass(entry)}`;
  d.dataset.level = level;
  const badge = document.createElement('span');
  badge.className = 'log-level-badge';
  badge.textContent = _levelLabel(level);
  const timeEl = document.createElement('span');
  timeEl.className = 'log-line-time';
  const clock = formatLogTime(entry.ts);
  timeEl.textContent = clock || '--:--:--';
  if (entry.ts != null) {
    timeEl.title = new Date(entry.ts * 1000).toLocaleString();
  }
  const text = document.createElement('span');
  text.className = 'log-line-text';
  text.textContent = entry.text;
  d.appendChild(badge);
  d.appendChild(timeEl);
  d.appendChild(text);
  view.appendChild(d);
}

function _paintLogsView(view) {
  if (!view) return;
  const buffer = _activeLogsBuffer();
  const visible = filterLogEntries(buffer, _logsFilterOpts());
  view.innerHTML = '';
  for (const entry of visible) {
    _appendLogLineEl(view, entry);
  }
  const empty = $('logs-empty-hint');
  if (empty) {
    const filteredOut = buffer.length > 0 && visible.length === 0;
    empty.hidden = !filteredOut;
  }
  const countEl = $('logs-match-count');
  if (countEl) {
    countEl.textContent = buffer.length
      ? t('logs.match_count', { visible: visible.length, total: buffer.length })
      : '';
  }
  if (_logsAutoScroll) view.scrollTop = view.scrollHeight;
}

function _syncLogsSourceUi(statusText = '') {
  const select = $('logs-source-select');
  if (select) select.value = _logsSource;

  const clearBtn = $('btn-logs-clear');
  if (clearBtn) {
    clearBtn.disabled = Boolean(_logsSource);
    clearBtn.title = _logsSource
      ? t('logs.clear_title_history')
      : t('logs.clear_title_live');
  }

  const status = $('logs-source-status');
  if (status) {
    status.textContent = statusText || (
      _logsSource
        ? historyStatusText({
          name: _logsSource,
          logs: _logsHistoryBuffer,
          truncated: _logsHistoryTruncated,
        })
        : t('logs.source_live')
    );
  }
}

function _syncLogsLevelChips() {
  document.querySelectorAll('[data-log-level]').forEach((btn) => {
    const on = (btn.dataset.logLevel || '') === _logsFilterLevel;
    btn.classList.toggle('is-active', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

function _syncLogsSinceChips() {
  document.querySelectorAll('[data-log-since]').forEach((btn) => {
    const ms = Number(btn.dataset.logSince || 0);
    const on = ms === _logsFilterSinceMs;
    btn.classList.toggle('is-active', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

/** Stop session-log polling without clearing the in-memory buffer (P2-P38). */
export function stopLogsPolling() {
  if (_logsTimer) {
    clearInterval(_logsTimer);
    _logsTimer = null;
  }
  _logsPollInFlightGen = null;
  _logsPollGeneration += 1;
  _logsHistorySeq += 1;
}

export function renderLogs() {
  const pane = $('tab-logs');
  stopLogsPolling();
  const sinceChipsHtml = _SINCE_CHIPS.map((c) =>
    `<button type="button" class="logs-chip" data-log-since="${c.ms}">${escapeHtml(t(c.key))}</button>`
  ).join('');
  pane.innerHTML = `
    <div class="logs-toolbar">
      <span class="logs-toolbar-title">${t('logs.title')}</span>
      <select id="logs-source-select" class="logs-source-select" aria-label="${t('logs.source_label')}">
        <option value="">${t('logs.source_current')}</option>
      </select>
      <span id="logs-source-status" class="logs-source-status muted" role="status"></span>
      <input type="search" id="logs-filter-query" class="logs-filter-input"
        placeholder="${t('logs.filter_placeholder')}" autocomplete="off"
        value="${escapeHtml(_logsFilterQuery)}"
        aria-label="${t('logs.filter_aria')}">
      <div class="logs-level-chips" role="group" aria-label="${t('logs.level_aria')}">
        <button type="button" class="logs-chip" data-log-level="all">${t('logs.level.all')}</button>
        <button type="button" class="logs-chip" data-log-level="info">${t('logs.level.info')}</button>
        <button type="button" class="logs-chip" data-log-level="warn">${t('logs.level.warn')}</button>
        <button type="button" class="logs-chip" data-log-level="error">${t('logs.level.error')}</button>
      </div>
      <div class="logs-since-chips" role="group" aria-label="${t('logs.since_aria')}">
        ${sinceChipsHtml}
      </div>
      <span id="logs-match-count" class="logs-match-count muted"></span>
      <label class="logs-autoscroll-label">
        <input type="checkbox" id="logs-autoscroll" ${_logsAutoScroll ? 'checked' : ''}> ${t('logs.autoscroll')}
      </label>
      <button type="button" class="btn-secondary" id="btn-logs-clear">${t('logs.clear')}</button>
    </div>
    <p id="logs-empty-hint" class="logs-empty-hint muted" hidden>${t('logs.empty_filtered')}</p>
    <div id="logs-view" class="logs-view" role="log" aria-live="polite"></div>
  `;
  const view = $('logs-view');
  const cb = $('logs-autoscroll');
  if (cb) cb.onchange = () => { _logsAutoScroll = cb.checked; };
  const queryInput = $('logs-filter-query');
  if (queryInput) {
    queryInput.oninput = () => {
      _logsFilterQuery = queryInput.value || '';
      _paintLogsView(view);
    };
  }
  pane.querySelectorAll('[data-log-level]').forEach((btn) => {
    btn.onclick = () => {
      _logsFilterLevel = btn.dataset.logLevel || 'all';
      _syncLogsLevelChips();
      _paintLogsView(view);
    };
  });
  pane.querySelectorAll('[data-log-since]').forEach((btn) => {
    btn.onclick = () => {
      _logsFilterSinceMs = Number(btn.dataset.logSince || 0);
      _syncLogsSinceChips();
      _paintLogsView(view);
    };
  });
  _syncLogsLevelChips();
  _syncLogsSinceChips();

  $('btn-logs-clear').onclick = async () => {
    if (_logsSource) return;
    try {
      await api('POST', '/api/logs/clear', {});
      _logsBuffer = [];
      _logsOffset = 0;
      _paintLogsView(view);
      _syncLogsSourceUi();
      void refreshSourceOptions();
    } catch { /* ignore */ }
  };

  const ingest = (lines) => {
    if (!Array.isArray(lines) || !lines.length) return;
    const prevLen = _logsBuffer.length;
    const entries = lines.map(normalizeLogEntry);
    appendLogEntries(_logsBuffer, entries);
    const trimmed = prevLen + entries.length > LOGS_BUFFER_MAX;
    // After ring trim, incremental DOM append would be wrong — full repaint.
    if (trimmed) {
      _paintLogsView(view);
      return;
    }
    for (const entry of entries) {
      if (entryMatchesLogFilter(entry, _logsFilterOpts())) {
        _appendLogLineEl(view, entry);
      }
    }
    const empty = $('logs-empty-hint');
    if (empty) {
      const visible = filterLogEntries(_logsBuffer, _logsFilterOpts());
      empty.hidden = !(_logsBuffer.length > 0 && visible.length === 0);
    }
    const countEl = $('logs-match-count');
    if (countEl) {
      const visible = filterLogEntries(_logsBuffer, _logsFilterOpts());
      countEl.textContent = t('logs.match_count', { visible: visible.length, total: _logsBuffer.length });
    }
    if (_logsAutoScroll) view.scrollTop = view.scrollHeight;
  };

  const pollOnce = async () => {
    if (_logsPollInFlightGen === _logsPollGeneration) return;
    const generation = _logsPollGeneration;
    _logsPollInFlightGen = generation;
    try {
      const r = await api('GET', `/api/logs?offset=${_logsOffset}`);
      if (!r || !r.logs) return;
      // Left the logs pane while in flight — do not touch detached DOM.
      if (!$('logs-view') || generation !== _logsPollGeneration || _logsSource) return;
      ingest(r.logs);
      if (typeof r.total === 'number') _logsOffset = r.total;
    } catch { /* ignore */ } finally {
      if (_logsPollInFlightGen === generation) _logsPollInFlightGen = null;
    }
  };

  const startLivePolling = () => {
    stopLogsPolling();
    if (_logsSource) return;
    _logsTimer = setInterval(pollOnce, 2000);
    void pollOnce();
  };

  const switchToLive = () => {
    stopLogsPolling();
    _logsSource = '';
    _logsHistoryBuffer = [];
    _logsHistoryTruncated = false;
    _syncLogsSourceUi();
    _paintLogsView(view);
    startLivePolling();
  };

  const loadHistoryFile = async (name) => {
    stopLogsPolling();
    const seq = ++_logsHistorySeq;
    _logsSource = name;
    _logsHistoryBuffer = [];
    _logsHistoryTruncated = false;
    _syncLogsSourceUi(t('logs.loading_history'));
    _paintLogsView(view);
    try {
      const r = await api('GET', `/api/logs/file?name=${encodeURIComponent(name)}`);
      if (seq !== _logsHistorySeq || !$('logs-view')) return;
      _logsHistoryBuffer = Array.isArray(r?.logs)
        ? r.logs.map(normalizeLogEntry)
        : [];
      _logsHistoryTruncated = Boolean(r?.truncated);
      _syncLogsSourceUi();
      _paintLogsView(view);
    } catch {
      if (seq !== _logsHistorySeq || !$('logs-view')) return;
      _logsHistoryBuffer = [];
      _logsHistoryTruncated = false;
      _syncLogsSourceUi(t('logs.history_load_failed'));
      _paintLogsView(view);
    }
  };

  const refreshSourceOptions = async () => {
    try {
      const r = await api('GET', '/api/logs/files');
      const select = $('logs-source-select');
      if (!select) return;
      const files = sortLogFiles(r?.files);
      const options = [`<option value="">${escapeHtml(t('logs.source_current'))}</option>`];
      for (const file of files) {
        options.push(
          `<option value="${escapeHtml(file.name)}">${escapeHtml(logFileLabel(file))}</option>`
        );
      }
      const currentMissing = _logsSource && !files.some((file) => file.name === _logsSource);
      if (currentMissing) {
        options.push(
          `<option value="${escapeHtml(_logsSource)}">${escapeHtml(t('logs.source_unavailable', { name: formatLogHour(_logsSource) }))}</option>`
        );
      }
      select.innerHTML = options.join('');
      select.value = _logsSource;
      _syncLogsSourceUi(currentMissing ? t('logs.history_missing') : '');
    } catch {
      if ($('logs-source-status')) _syncLogsSourceUi(t('logs.history_list_failed'));
    }
  };

  const sourceSelect = $('logs-source-select');
  if (sourceSelect) {
    sourceSelect.onchange = () => {
      const selected = sourceSelect.value || '';
      if (!selected) {
        switchToLive();
      } else {
        void loadHistoryFile(selected);
      }
    };
  }

  // Re-enter: show the retained source immediately. Live mode resumes from offset;
  // historical mode stays paused and read-only.
  _paintLogsView(view);
  _syncLogsSourceUi();
  void refreshSourceOptions();
  if (!_logsSource) startLivePolling();
}


export async function renderTokens() {
  const pane = $('tab-tokens');
  pane.innerHTML = `<p class="muted">${t('common.loading')}</p>`;
  try {
    const data = await api('GET', '/api/token-usage');
    if (!data || !data.total) {
      pane.innerHTML = `<p class="muted">${t('tokens.empty')}</p>`;
      return;
    }
    const totals = data.total;
    const totalHtml = `
      <div style="display:flex;gap:var(--space-3);margin-bottom:var(--space-3);flex-wrap:wrap">
        <div class="token-card"><div class="token-card-value">${totals.total_tokens.toLocaleString()}</div><div class="token-card-label">${t('tokens.total_label')}</div></div>
        <div class="token-card"><div class="token-card-value">${totals.prompt_tokens.toLocaleString()}</div><div class="token-card-label">Prompt</div></div>
        <div class="token-card"><div class="token-card-value">${totals.completion_tokens.toLocaleString()}</div><div class="token-card-label">Completion</div></div>
      </div>`;

    let modelHtml = `<h4 style="margin:var(--space-2) 0">${t('tokens.by_model')}</h4><table class="token-table"><tr><th>${t('tokens.col.model')}</th><th>${t('tokens.col.calls')}</th><th>Prompt</th><th>Completion</th><th>${t('tokens.col.total')}</th></tr>`;
    for (const [model, m] of Object.entries(data.by_model || {})) {
      modelHtml += `<tr><td>${escapeHtml(model)}</td><td>${m.calls}</td><td>${m.prompt_tokens.toLocaleString()}</td><td>${m.completion_tokens.toLocaleString()}</td><td>${m.total_tokens.toLocaleString()}</td></tr>`;
    }
    modelHtml += '</table>';

    let taskHtml = `<h4 style="margin:var(--space-2) 0">${t('tokens.by_task')}</h4><table class="token-table"><tr><th>${t('tokens.col.task')}</th><th>${t('tokens.col.calls')}</th><th>Prompt</th><th>Completion</th><th>${t('tokens.col.total')}</th></tr>`;
    for (const [task, tk] of Object.entries(data.by_task || {})) {
      taskHtml += `<tr><td>${escapeHtml(task)}</td><td>${tk.calls}</td><td>${tk.prompt_tokens.toLocaleString()}</td><td>${tk.completion_tokens.toLocaleString()}</td><td>${tk.total_tokens.toLocaleString()}</td></tr>`;
    }
    taskHtml += '</table>';

    let historyHtml = `<h4 style="margin:var(--space-2) 0">${t('tokens.history')}</h4><table class="token-table"><tr><th>${t('tokens.col.time')}</th><th>${t('tokens.col.task')}</th><th>${t('tokens.col.model')}</th><th>Prompt</th><th>Completion</th><th>${t('tokens.col.total')}</th></tr>`;
    for (const h of (data.history || []).slice().reverse().slice(0, 100)) {
      historyHtml += `<tr><td class="token-time">${escapeHtml(h.timestamp || '')}</td><td>${escapeHtml(h.task || '')}</td><td>${escapeHtml(h.model || '')}</td><td>${(h.prompt_tokens || 0).toLocaleString()}</td><td>${(h.completion_tokens || 0).toLocaleString()}</td><td>${(h.total_tokens || 0).toLocaleString()}</td></tr>`;
    }
    historyHtml += '</table>';

    pane.innerHTML = `<div style="padding:var(--space-2)">${totalHtml}${modelHtml}${taskHtml}${historyHtml}</div>`;
  } catch (e) {
    const message = t('tokens.load_failed', { detail: e.message || e });
    pane.innerHTML = `<p class="muted">${escapeHtml(message)}</p>`;
    setStatus(message, 'err');
  }
}


let _installPollTimer = null;
let _installTaskId = null;
let _installTaskUnsubscribe = null;

function _clearWhisperTaskSubscription() {
  if (_installTaskUnsubscribe) {
    _installTaskUnsubscribe();
    _installTaskUnsubscribe = null;
  }
  _installTaskId = null;
}

function _subscribeWhisperTask(taskId) {
  _clearWhisperTaskSubscription();
  if (!taskId) return;
  _installTaskId = taskId;
  _installTaskUnsubscribe = subscribeTaskEvents(payload => {
    const task = payload?.task;
    if (!task || task.id !== taskId || task.kind !== 'whisper_install') return;
    _applyWhisperTask(task, payload.event);
  });
  fetchTask(taskId).then(task => {
    if (task && task.id === taskId) _applyWhisperTask(task);
  }).catch(() => {});
}

function _applyWhisperTask(task, event = null) {
  const prog = $('model-dl-progress');
  const bar = $('model-dl-bar');
  const msg = $('model-dl-msg');
  const cancelBtn = $('btn-cancel-dl');
  const dlBtn = $('btn-model-download');
  const pct = Number.isFinite(task.progress_pct) ? task.progress_pct : 0;
  if (prog) prog.style.display = task.status === 'queued' || task.status === 'running' || task.status === 'cancelling' ? 'block' : 'none';
  if (bar) bar.style.width = `${Math.min(100, Math.max(0, pct))}%`;
  if (msg) msg.textContent = event?.message || task.message || task.phase || t('whisper_mgmt.downloading');
  if (cancelBtn) cancelBtn.style.display = task.status === 'queued' || task.status === 'running' || task.status === 'cancelling' ? '' : 'none';
  if (dlBtn && (task.status === 'queued' || task.status === 'running' || task.status === 'cancelling')) {
    dlBtn.disabled = true;
    dlBtn.textContent = task.status === 'cancelling' ? t('whisper_mgmt.cancelling') : t('whisper_mgmt.downloading');
  }
  if (!['succeeded', 'failed', 'cancelled', 'interrupted'].includes(task.status)) return;
  if (_installPollTimer) { clearInterval(_installPollTimer); _installPollTimer = null; }
  _clearWhisperTaskSubscription();
  if (dlBtn) { dlBtn.disabled = false; dlBtn.innerHTML = `${icon('download', 14)} ${t('whisper_mgmt.btn.download')}`; }
  if (task.status === 'succeeded') {
    setStatus(t('whisper_mgmt.download_done_status'), 'ok', { persist: false });
    _loadModelMgmt();
  } else if (task.status === 'cancelled') {
    setStatus(t('whisper_mgmt.download_cancelled'), 'warn');
  } else {
    setStatus(t('whisper_mgmt.download_failed', { detail: task.error_message || task.message || t('common.unknown_error') }), 'err');
  }
}

function renderModelManagement() {
  const div = document.createElement('div');
  div.id = 'whisper-model-mgmt';
  div.style.cssText = 'padding:0;background:transparent;border:none;border-radius:0';
  div.innerHTML = `
    <div id="model-mgmt-content">
      <p class="muted">${t('common.loading')}</p>
    </div>
  `;
  return div;
}


async function _loadModelMgmt() {
  const container = $('model-mgmt-content');
  if (!container) {
    setStatus(t('whisper_mgmt.dom_not_ready'), 'warn');
    return;
  }
  const timeoutId = setTimeout(() => {
    if (container && container.querySelector('.muted')) {
      container.innerHTML = `<p class="err">${t('whisper_mgmt.timeout_detail')}</p>`;
      setStatus(t('whisper_mgmt.timeout_status'), 'err');
    }
  }, 10000);
  try {
    const [data, check] = await Promise.all([
      api('GET', '/api/whisper/models'),
      api('GET', '/api/whisper/check').catch(() => ({ cublas: true })),
    ]);
    if (!data.ok) {
      clearTimeout(timeoutId);
      container.innerHTML = `<p class="err">${t('whisper_mgmt.list_failed')}</p>`;
      setStatus(data.error || t('whisper_mgmt.list_failed'), 'err');
      return;
    }

    const current = data.current_model || 'medium';
    const avail = data.available || [];
    const cached = data.cached || [];
    const cublasOk = check.cublas !== false;
    const frozen = check.frozen === true;

    const engineNotice = transcriptionModelManagementNotice(check);
    let html = engineNotice
      ? `<p class="hint" style="margin:0 0 10px">${escapeHtml(engineNotice)}</p>`
      : '';
    html += '<div style="display:flex;gap:12px;flex-wrap:wrap;align-items:end">';

    html += '<div style="flex:1;min-width:140px">';
    html += `<label style="font-size:var(--text-xs);color:var(--text-secondary)">${t('whisper_mgmt.current_model')}</label>`;
    html += '<select id="model-size-select" style="width:100%;margin-top:2px">';
    for (const m of avail) {
      const sel = m.name === current ? ' selected' : '';
      const cachedIcon = cached.some(c => c.name === m.name && c.valid) ? ' ✓' : '';
      html += `<option value="${escapeHtml(m.name)}"${sel}>${escapeHtml(m.label)}${cachedIcon}</option>`;
    }
    html += '</select></div>';

    html += '<div>';
    const alreadyCached = cached.some(c => c.name === current && c.valid);
    const needsReverify = alreadyCached && !cublasOk;
    let btnText;
    let btnDisabled;
    if (frozen) {
      btnText = t('whisper_mgmt.btn.frozen');
      btnDisabled = ' disabled';
    } else {
      btnText = needsReverify ? t('whisper_mgmt.btn.reverify') : alreadyCached ? t('whisper_mgmt.btn.cached') : t('whisper_mgmt.btn.download');
      btnDisabled = alreadyCached && !needsReverify ? ' disabled' : '';
    }
    html += `<button id="btn-model-download" class="btn-primary" style="font-size:var(--text-sm)"${btnDisabled}>${icon('download', 14)} ${btnText}</button>`;
    if (needsReverify) {
      html += `<p style="font-size:var(--text-xs);color:var(--warn,#b8860b);margin:4px 0 0">${t('whisper_mgmt.cublas_warn')}</p>`;
    }
    if (frozen) {
      html += `<p style="font-size:var(--text-xs);color:var(--warn,#b8860b);margin:4px 0 0">${t('whisper_mgmt.frozen_warn')}</p>`;
    }
    html += '</div>';

    html += '<div style="font-size:var(--text-xs);color:var(--text-secondary);white-space:nowrap">';
    html += t('whisper_mgmt.free_space', { size: escapeHtml(data.free_display || '?') });
    html += '</div>';

    html += '</div>';

    if (cached.length) {
      html += '<div style="margin-top:8px">';
      html += `<p style="font-size:var(--text-xs);color:var(--text-secondary);margin:0 0 4px">${t('whisper_mgmt.cached_models')}</p>`;
      for (const m of cached) {
        const validCls = m.valid ? 'ok' : 'err';
        html += '<div style="display:flex;gap:8px;align-items:center;padding:4px 0;font-size:var(--text-xs)">';
        html += `<span style="flex:1">${escapeHtml(m.name)} <span class="${validCls}">${m.valid ? '✓' : t('whisper_mgmt.incomplete')}</span> <span class="muted">${escapeHtml(m.size_display)}</span></span>`;
        html += `<button class="btn-delete-model" data-model="${escapeHtml(m.name)}" style="background:none;border:1px solid var(--err,#c44);color:var(--err,#c44);padding:2px 8px;border-radius:3px;cursor:pointer;font-size:var(--text-xs)">${t('common.delete')}</button>`;
        html += '</div>';
      }
      html += '</div>';
    } else {
      html += `<p class="muted" style="margin-top:8px;font-size:var(--text-xs)">${t('whisper_mgmt.no_cached')}</p>`;
    }

    html += '<div id="model-dl-progress" style="display:none;margin-top:8px">';
    html += '<div style="display:flex;justify-content:space-between;font-size:var(--text-xs);margin-bottom:4px">';
    html += '<span id="model-dl-msg"></span><span id="model-dl-speed"></span></div>';
    html += '<div style="background:#333;border-radius:3px;height:6px;overflow:hidden">';
    html += '<div id="model-dl-bar" style="background:var(--accent);border-radius:3px;height:100%;width:0%"></div></div>';
    html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-top:2px">';
    html += '<p id="model-dl-eta" class="muted" style="font-size:var(--text-xs);margin:0"></p>';
    html += `<button id="btn-cancel-dl" style="background:none;border:1px solid var(--err,#c44);color:var(--err,#c44);padding:2px 10px;border-radius:3px;cursor:pointer;font-size:var(--text-xs);display:none">${t('whisper_mgmt.cancel_download')}</button>`;
    html += '</div>';
    html += '</div>';

    container.innerHTML = html;

    const sel = $('model-size-select');
    if (sel) {
      sel.onchange = async () => {
        const newModel = sel.value;
        const dlBtn = $('btn-model-download');
        const isCached = cached.some(c => c.name === newModel && c.valid);
        const needsReverify = isCached && !cublasOk;
        if (dlBtn) {
          dlBtn.disabled = isCached && !needsReverify;
          dlBtn.innerHTML = isCached
            ? (needsReverify ? t('whisper_mgmt.btn.reverify') : t('whisper_mgmt.btn.cached'))
            : `${icon('download', 14)} ${t('whisper_mgmt.btn.download')}`;
        }
        try {
          const r = await api('PUT', '/api/whisper/model', { model_size: newModel });
          if (r.ok) {
            setStatus(t('whisper_mgmt.switched', { model: newModel }), 'ok');
          } else {
            setStatus(t('whisper_mgmt.switch_failed', { detail: r.error || '' }), 'err');
            sel.value = current;
          }
        } catch (e) {
          setStatus(t('whisper_mgmt.switch_failed', { detail: e.message }), 'err');
          sel.value = current;
        }
      };
    }

    const dlBtn = $('btn-model-download');
    if (dlBtn) {
      dlBtn.onclick = async () => {
        if (check.frozen === true) {
          setStatus(t('whisper_mgmt.frozen_status'), 'warn');
          return;
        }
        dlBtn.disabled = true;
        dlBtn.textContent = t('whisper_mgmt.starting');
        const prog = $('model-dl-progress');
        if (prog) prog.style.display = 'block';
        const cancelBtn = $('btn-cancel-dl');
        if (cancelBtn) cancelBtn.style.display = '';
        try {
          const r = await api('POST', '/api/whisper/install', {});
          if (!r.ok) throw new Error(r.error || t('whisper_mgmt.start_failed_bare'));
          dlBtn.textContent = t('whisper_mgmt.downloading');
          if (r.task_id || r.task?.id) {
            if (_installPollTimer) { clearInterval(_installPollTimer); _installPollTimer = null; }
            _subscribeWhisperTask(r.task_id || r.task.id);
            _applyWhisperTask(r.task || { id: r.task_id, kind: 'whisper_install', status: 'queued' });
          } else {
            _installPollTimer = setInterval(_pollModelDl, 1000);
            _pollModelDl();
          }
        } catch (e) {
          dlBtn.disabled = false;
          dlBtn.innerHTML = `${icon('download', 14)} ${t('whisper_mgmt.btn.download')}`;
          const progMsg = $('model-dl-msg');
          const message = t('whisper_mgmt.start_failed', { detail: e.message });
          if (progMsg) progMsg.textContent = message;
          void setStatus(message, 'err');
        }
      };
    }

    const cancelBtn = $('btn-cancel-dl');
    if (cancelBtn) {
      cancelBtn.onclick = async () => {
        const managedCancel = Boolean(_installTaskId);
        try {
          if (_installTaskId) await api('POST', `/api/tasks/${encodeURIComponent(_installTaskId)}/cancel`, {});
          else await api('POST', '/api/whisper/install/cancel', {});
        } catch { /* ignore */ }
        if (managedCancel) {
          cancelBtn.disabled = true;
          cancelBtn.textContent = t('whisper_mgmt.cancelling');
          return;
        }
        if (_installPollTimer) { clearInterval(_installPollTimer); _installPollTimer = null; }
        const prog = $('model-dl-progress');
        if (prog) prog.style.display = 'none';
        cancelBtn.style.display = 'none';
        const dlBtn = $('btn-model-download');
        if (dlBtn) { dlBtn.disabled = false; dlBtn.innerHTML = `${icon('download', 14)} ${t('whisper_mgmt.btn.download')}`; }
      };
    }

    container.querySelectorAll('.btn-delete-model').forEach(btn => {
      btn.onclick = async () => {
        const modelName = btn.dataset.model;
        if (!confirm(t('whisper_mgmt.delete_confirm', { name: modelName }))) return;
        try {
          const r = await api('POST', '/api/whisper/models/delete', { name: modelName });
          if (r.ok) {
            setStatus(t('whisper_mgmt.deleted', { name: modelName }), 'ok');
            _loadModelMgmt();
          } else {
            setStatus(t('whisper_mgmt.delete_failed', { detail: r.error || '' }), 'err');
          }
        } catch (e) {
          setStatus(t('whisper_mgmt.delete_failed', { detail: e.message }), 'err');
        }
      };
    });
    clearTimeout(timeoutId);
    try {
      const tasksResult = await api('GET', '/api/tasks?kind=whisper_install&visibility=all&limit=20');
      const managed = (tasksResult.tasks || []).find(task => ['queued', 'running', 'cancelling'].includes(task.status)
        && (!state.currentProjectDir || !task.project_id || task.project_id === state.currentProjectDir));
      if (managed) {
        const prog = $('model-dl-progress');
        if (prog) prog.style.display = 'block';
        const cancelBtn = $('btn-cancel-dl');
        if (cancelBtn) cancelBtn.style.display = '';
        const dlBtn = $('btn-model-download');
        if (dlBtn) { dlBtn.disabled = true; dlBtn.textContent = t('whisper_mgmt.downloading'); }
        if (_installPollTimer) { clearInterval(_installPollTimer); _installPollTimer = null; }
        _subscribeWhisperTask(managed.id);
        _applyWhisperTask(managed);
      }
    } catch {
      try {
        const st = await api('GET', '/api/whisper/install/status');
        if (st.running || st.status === 'downloading') {
          if (_installPollTimer) clearInterval(_installPollTimer);
          _installPollTimer = setInterval(_pollModelDl, 1000);
          _pollModelDl();
        }
      } catch { /* compatibility resume is not critical */ }
    }
  } catch (e) {
    clearTimeout(timeoutId);
    const message = t('tokens.load_failed', { detail: e.message });
    if (container) container.innerHTML = `<p class="err">${escapeHtml(message)}</p>`;
    setStatus(message, 'err');
  }
}


async function _pollModelDl() {
  try {
    const s = await api('GET', '/api/whisper/install/status');
    const bar = $('model-dl-bar');
    const msg = $('model-dl-msg');
    const speed = $('model-dl-speed');
    const eta = $('model-dl-eta');
    const dlBtn = $('btn-model-download');
    const cancelBtn = $('btn-cancel-dl');
    if (!s.running && s.status === 'idle') {
      if (_installPollTimer) { clearInterval(_installPollTimer); _installPollTimer = null; }
      if (cancelBtn) cancelBtn.style.display = 'none';
      return;
    }
    if (s.status === 'downloading') {
      if (cancelBtn) cancelBtn.style.display = '';
      if (bar) bar.style.width = (s.progress_pct || 0) + '%';
      if (msg) msg.textContent = s.message || t('whisper_mgmt.downloading');
      if (speed && s.speed) speed.textContent = s.speed;
      if (eta) {
        if (s.eta_sec != null) {
          const m = Math.floor(s.eta_sec / 60);
          const sec = s.eta_sec % 60;
          eta.textContent = t('whisper_mgmt.eta', { min: m, sec });
        } else { eta.textContent = ''; }
      }
    } else if (s.status === 'done') {
      if (_installPollTimer) { clearInterval(_installPollTimer); _installPollTimer = null; }
      if (cancelBtn) cancelBtn.style.display = 'none';
      if (bar) bar.style.width = '100%';
      if (msg) msg.textContent = t('whisper_mgmt.done_msg');
      void setStatus(t('whisper_mgmt.download_done_status'), 'ok');
      if (eta) eta.textContent = '';
      if (dlBtn) { dlBtn.disabled = false; dlBtn.innerHTML = `${icon('download', 14)} ${t('whisper_mgmt.btn.download')}`; }
      _loadModelMgmt();
    } else if (s.status === 'error') {
      if (_installPollTimer) { clearInterval(_installPollTimer); _installPollTimer = null; }
      if (cancelBtn) cancelBtn.style.display = 'none';
      if (dlBtn) { dlBtn.disabled = false; dlBtn.innerHTML = `${icon('download', 14)} ${t('whisper_mgmt.retry_download')}`; }
      const message = s.message || t('whisper_mgmt.failed_bare');
      if (msg) msg.textContent = message;
      void setStatus(message, 'err');
    }
  } catch { /* ignore */ }
}
