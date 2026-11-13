/**
 * Config schema, field rendering, section cards, and form handlers.
 */
import { api, icon } from './api.js';
import { state } from './state.js';
import { escapeHtml, markDirty, setDeep } from './utils.js';
import { hasKey, t } from './i18n.js';
import { renderProviderList } from './editor-providers.js';
import { renderTaskBinding } from './editor-task-binding.js';

function _renderTaskBinding(tasks, providersObj, descs, opts = {}) {
  return renderTaskBinding(tasks, providersObj, descs, opts, _renderTooltip);
}
const ADVANCED_TOGGLE_KEY = 'vlog-config-show-advanced';
let _configSchema = null;

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

export const FIELD_LABELS = {
  // plan
  'plan.plans_subdir': '规划子目录',
  'plan.max_clips_per_day': '每日最多素材数',
  'plan.max_segments': '每日最多片段数',
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

export function setSectionCollapsed(sectionKey, collapsed) {
  _collapsedCards.set(sectionKey, collapsed);
}

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
    <div class="config-card-body">${renderProviderList(providers || {}, descs)}</div>
  </div>`;
  return html;
}

export function _renderConfigProject(projectData, globalData, descs) {
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

export function _attachContextTemplate(pane) {
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
