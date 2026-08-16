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
// Dynamic import avoids static cycle: editor.js → editor-config.js → editor.js (A-006)

const DEFAULT_PROVIDERS = ['gemini', 'openai', 'deepseek'];
const DEFAULT_MODELS = {
  gemini: ['gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-3-flash', 'gemini-3.1-flash-lite', 'gemini-3.5-flash'],
  openai: ['gpt-4o', 'gpt-4o-mini'],
  deepseek: ['deepseek-chat', 'deepseek-reasoner', 'deepseek-v4-flash', 'deepseek-v4-pro'],
};
const DEFAULT_CAPABILITIES = {
  gemini: ['video', 'text'],
  openai: ['text'],
};

const PROMPT_LABELS = {
  ANALYZE_PROMPT: '视频分析',
  SCRIPT_PROMPT: '口播文案',
  PLAN_PROMPT: '剪辑规划',
  REFINE_TEXT_PROMPT: '素材精修',
  REFINE_TEXT_FIX_PROMPT: '素材定向修复',
  REFINE_SCRIPT_PROMPT: '脚本精修',
  REFINE_SCRIPT_FIX_PROMPT: '脚本定向修复',
};


const FIELD_LABELS = {
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
  // provider
  'max_tokens': 'max_tokens（0=不限制）',
  'timeout_sec': 'HTTP 超时（秒）',
  'retry_attempts': '额外重试次数',
  'requests_per_minute': '每分钟请求上限（0=不限）',
  'poll_interval_sec': '轮询间隔（秒）',
};


export function labelFromPath(path) {
  if (!path) return 'config';
  if (FIELD_LABELS[path]) return FIELD_LABELS[path];
  const leaf = path.split('.').pop();
  if (FIELD_LABELS[leaf]) return FIELD_LABELS[leaf];
  return leaf;
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
  input.placeholder = '输入模型名，按回车添加';
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


export function _renderConfigForm(obj, path, descriptions = null) {
  if (obj === null || obj === undefined) {
    return `<span class="config-null">(空)</span>`;
  }
  const descPath = _resolveDescPath(path, descriptions);
  const tip = descPath ? _renderTooltip(path, descriptions[descPath]) : '';
  if (typeof obj === 'boolean') {
    return `<label class="config-field config-bool"><span class="config-key">${labelFromPath(path)}${tip}</span> <input type="checkbox" data-path="${path}" ${obj ? 'checked' : ''}></label>`;
  }
  if (typeof obj === 'number') {
    const isInt = Number.isInteger(obj);
    return `<label class="config-field config-num"><span class="config-key">${labelFromPath(path)}${tip}</span> <input type="number" data-path="${path}" step="${isInt ? '1' : 'any'}" value="${obj}"></label>`;
  }
  if (typeof obj === 'string') {
    const multiline = path === 'ai.context' || obj.length > 80 || obj.includes('\n');
    if (multiline) {
      let hint = '';
      if (path === 'ai.context') {
        hint = '<br><span class="hint">项目特定背景（如拍摄地点、行程安排），将追加到默认模板 <code>trip_context.md</code> 之后。留空则仅使用默认模板。</span>';
      }
      return `<label class="config-field config-str"><span class="config-key">${labelFromPath(path)}${tip}</span> <textarea data-path="${path}" rows="4">${escapeHtml(obj)}</textarea>${hint}</label>`;
    }
    const isPwd = path.endsWith('api_key');
    let hint = '';
    if (path.endsWith('api_key_env')) {
      hint = '<br><span class="hint">环境变量名称（如 <code>GEMINI_API_KEY</code>），实际密钥值写在项目根目录的 <code>.env</code> 文件中</span>';
    } else if (path.endsWith('base_url')) {
      hint = '<br><span class="hint">API 基础地址。Gemini 留空即可；OpenAI 兼容接口必填，如 <code>https://api.deepseek.com/v1</code></span>';
    } else if (path.endsWith('api_key')) {
      hint = '<br><span class="hint">API 密钥（直接填入）。<strong>不推荐</strong>，建议使用 <code>api_key_env</code> 配合 <code>.env</code> 文件更安全</span>';
    }
    const pickKind = pathPickKind(path);
    const browseBtn = pickKind
      ? `<button type="button" class="browse-btn" data-desktop-browse data-pick-kind="${pickKind}" data-target-path="${path}">浏览</button>`
      : '';
    const fieldInput = browseBtn
      ? `<span class="input-with-browse"><input type="${isPwd ? 'password' : 'text'}" data-path="${path}" value="${escapeHtml(obj)}">${browseBtn}</span>`
      : `<input type="${isPwd ? 'password' : 'text'}" data-path="${path}" value="${escapeHtml(obj)}">`;
    return `<label class="config-field config-str"><span class="config-key">${labelFromPath(path)}${tip}</span> ${fieldInput}</label>${hint}`;
  }
  if (Array.isArray(obj)) {
    const allStr = obj.every(x => typeof x === 'string');
    if (allStr) {
      return `<fieldset class="config-fieldset"><legend>${labelFromPath(path)}</legend><label class="config-field config-str"><textarea data-path="${path}" rows="${Math.max(2, obj.length)}">${escapeHtml(obj.join('\n'))}</textarea><span class="hint">每行一项</span></label></fieldset>`;
    }
    return `<fieldset class="config-fieldset"><legend>${labelFromPath(path)}</legend>${obj.map((item, i) =>
      `<div class="config-array-item">${_renderConfigForm(item, path + '[' + i + ']', descriptions)}</div>`
    ).join('')}</fieldset>`;
  }
  if (typeof obj === 'object') {
    let html = `<fieldset class="config-fieldset"><legend>${labelFromPath(path) || '配置'}${tip}</legend>`;
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

const _SECTION_LABELS = {
  proxy: '代理',
  server: '服务',
  naming: '编号命名',
  paths: '路径',
  compress: '压缩',
  whisper: 'Whisper 语音识别',
  ai: 'AI 配置',
  analyze: 'AI 分析',
  script: '口播脚本',
  plan: '剪辑规划',
  export: '导出设置',
};

function _isSectionCollapsed(key, isGlobal) {
  if (isGlobal) {
    return ['proxy', 'compress', 'whisper'].includes(key);
  }
  return ['analyze', 'script', 'whisper', 'export'].includes(key);
}

function _renderSectionCard(key, val, descs, isGlobal) {
  const label = _SECTION_LABELS[key] || key;
  const sectionKey = (isGlobal ? 'global.' : 'project.') + key;
  const wasCollapsed = _collapsedCards.get(sectionKey);
  const collapsed = wasCollapsed !== undefined ? wasCollapsed : _isSectionCollapsed(key, isGlobal);
  let fieldsHtml = '';
  if (typeof val === 'object' && val !== null && !Array.isArray(val)) {
    for (const [k, v] of Object.entries(val)) {
      fieldsHtml += _renderConfigForm(v, `${key}.${k}`, descs);
    }
  } else {
    fieldsHtml = _renderConfigForm(val, key, descs);
  }
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
    if (btn) { btn.disabled = true; btn.textContent = '创建中...'; }
    const r = await api('POST', '/api/config/init', {});
    if (r.ok) {
      setStatus('项目配置文件已创建', 'ok');
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
      setStatus('创建失败: ' + (r.error || '未知错误'), 'err');
    }
  } catch (e) {
    setStatus('创建失败: ' + e.message, 'err');
  } finally {
    const btn = $('btn-config-init');
    if (btn) { btn.disabled = false; btn.textContent = '为该项目创建配置文件'; }
  }
}


function _renderConfigGlobal(globalData, descs) {
  const order = ['paths', 'server', 'naming', 'proxy', 'compress', 'whisper'];
  let html = '';
  for (const key of order) {
    if (key in globalData) {
      html += _renderSectionCard(key, globalData[key], descs, true);
    }
  }
  const aiCfg = globalData.ai || {};
  const { providers, ...otherAiFields } = aiCfg;
  if (Object.keys(otherAiFields).length > 0) {
    html += _renderSectionCard('ai', otherAiFields, descs, true);
  }
  html += `<div class="config-card" data-section-key="global.providers">
    <div class="config-card-header" tabindex="0" role="button">
      <span class="config-card-arrow">▶</span>
      <span class="config-card-title">AI 模型列表</span>
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
      <span class="config-card-title">AI 任务绑定</span>
    </div>
    <div class="config-card-body">${_renderTaskBinding(
      tasks || {},
      globalData?.ai?.providers || {},
      descs || {},
    )}</div>
  </div>`;
  return html;
}

function _promptLabel(name) {
  return PROMPT_LABELS[name] || name;
}

function _promptSourceLabel(item) {
  if (!item?.has_override) return '系统默认';
  const source = item.source_path || item.override_path || '';
  return source.includes('templates') ? '覆盖文件' : '项目覆盖';
}

export function _renderPromptManagement(payload, selectedName = null) {
  const prompts = payload?.prompts || [];
  if (!prompts.length) {
    return '<div class="config-empty-state"><p class="muted">暂无可编辑 Prompt。</p></div>';
  }
  const selected = prompts.find(p => p.name === selectedName) || prompts[0];
  const listHtml = prompts.map(p => {
    const active = p.name === selected.name ? ' active' : '';
    const badge = p.has_override ? '<span class="prompt-badge override">已覆盖</span>' : '<span class="prompt-badge">默认</span>';
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
          <button id="btn-prompt-save" class="btn-primary">${icon('save', 14)} 保存覆盖</button>
          <button id="btn-prompt-restore" class="btn-secondary" ${selected.has_override ? '' : 'disabled'}>${icon('refresh', 14)} 恢复默认</button>
        </div>
      </div>
      <textarea id="prompt-editor-text" class="prompt-editor-text" spellcheck="false">${escapeHtml(selected.content || '')}</textarea>
      <div class="prompt-meta">
        <span>保存路径: ${escapeHtml(selected.override_path || '')}</span>
        ${sourcePath ? `<span>当前来源: ${escapeHtml(sourcePath)}</span>` : ''}
      </div>
      <details class="prompt-default-preview">
        <summary>查看系统默认 Prompt</summary>
        <pre>${escapeHtml(selected.default || '')}</pre>
      </details>
    </div>
  </div>`;
}

async function _loadPromptManagement() {
  const container = $('prompt-management-root');
  if (!container) return;
  container.innerHTML = '<p class="muted">加载 Prompt...</p>';
  try {
    state.promptPayload = await api('GET', '/api/prompts');
    const prompts = state.promptPayload?.prompts || [];
    if (!state.currentPromptName && prompts.length) {
      state.currentPromptName = prompts[0].name;
    }
    _refreshPromptManagement();
  } catch (e) {
    container.innerHTML = `<p class="err">加载失败: ${escapeHtml(e.message || e)}</p>`;
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
        setStatus('Prompt 内容不能为空', 'err');
        return;
      }
      saveBtn.disabled = true;
      try {
        await api('PUT', `/api/prompts/${encodeURIComponent(selected.name)}`, { content });
        setStatus('Prompt 覆盖已保存，下一次 AI 调用生效', 'ok');
        state.promptPayload = await api('GET', '/api/prompts');
        _refreshPromptManagement();
      } catch (e) {
        setStatus('保存失败: ' + (e.message || e), 'err');
      } finally {
        saveBtn.disabled = false;
      }
    };
  }
  if (restoreBtn && selected) {
    restoreBtn.onclick = async () => {
      if (!selected.has_override) return;
      if (!confirm(`恢复 ${selected.name} 的项目级 Prompt 覆盖？`)) return;
      restoreBtn.disabled = true;
      try {
        await api('DELETE', `/api/prompts/${encodeURIComponent(selected.name)}`);
        setStatus('Prompt 覆盖已恢复默认', 'ok');
        state.promptPayload = await api('GET', '/api/prompts');
        _refreshPromptManagement();
      } catch (e) {
        setStatus('恢复失败: ' + (e.message || e), 'err');
      } finally {
        restoreBtn.disabled = false;
      }
    };
  }
}

const TASK_LABELS = {
  video_analyze: '视频分析',
  voiceover: '口播文案',
  vlog_plan: 'vlog 剪辑规划',
  refine_text: '文本精修',
};
const TASK_DESCRIPTIONS = {
  video_analyze: '分析视频内容（场景、物体、人物、动作、地点），需多模态模型',
  voiceover: '根据分析结果生成口播文案',
  vlog_plan: '根据所有素材生成剪辑顺序和时间轴',
  refine_text: '对已有文案进行润色和修正（默认跟随视频分析）',
};

export function _renderTaskBinding(tasks, providersObj, descs) {
  const providerKeys = Object.keys(providersObj || {});
  let html = '<fieldset class="config-fieldset"><legend>AI 任务绑定</legend>';
  html += '<p class="hint" style="margin:0 0 8px;line-height:1.6">为流水线的每个步骤指定使用的 AI Provider 和模型。<br>视频分析（video_analyze）<strong>必须</strong>使用 type=Gemini 的 Provider（支持多模态）；其他任务可以使用任意 Provider。<br>勾选"跟随视频分析"表示该任务复用 video_analyze 的配置；取消勾选后可独立选择。</p>';

  if (providerKeys.length === 0) {
    html += '<div class="config-empty-state"><p class="muted">还没有注册任何 AI 模型</p><p class="hint">请先在"全局"标签页中添加 Provider。</p><p><a href="#" id="goto-global-providers">去添加 →</a></p></div>';
    html += '</fieldset>';
    return html;
  }

  for (const taskKey of ['video_analyze', 'voiceover', 'vlog_plan', 'refine_text']) {
    const label = TASK_LABELS[taskKey];
    const desc = TASK_DESCRIPTIONS[taskKey];
    const taskCfg = tasks[taskKey];
    const isRefine = taskKey === 'refine_text';
    const isFollowing = isRefine && !taskCfg;

    html += '<div class="task-binding-card">';
    html += '<div class="task-binding-header">';
    html += `<span class="task-binding-name">${escapeHtml(label)}${_renderTooltip('ai.tasks.' + taskKey, desc)}</span>`;
    html += '</div>';

    if (isRefine) {
      html += '<div class="task-binding-row">';
      html += '<label class="task-binding-label refine-follow-cb">';
      html += `<input type="checkbox" class="refine-follow-check" data-task="${taskKey}" ${isFollowing ? 'checked' : ''}> 跟随视频分析`;
      html += '</label>';
      html += '<span class="hint" style="font-size:var(--text-xs)">勾选 = 使用 video_analyze 相同的 Provider/模型；取消 = 可独立指定其他 Provider</span>';
      html += '</div>';
    }

    if (isFollowing) {
      const vaCfg = tasks.video_analyze || {};
      html += '<div class="task-binding-row">';
      html += `<span class="muted">继承自 video_analyze: ${escapeHtml(vaCfg.provider || '(未配置)')} / ${escapeHtml(vaCfg.model || '(未配置)')}</span>`;
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
      html += '<option value="">-- 选择 Provider --</option>';
      for (const pk of eligibleProviders) {
        const p = providersObj[pk];
        const typeLabel = p?.type === 'gemini' ? 'Gemini' : 'OpenAI 兼容';
        const selected = pk === currentProvider ? ' selected' : '';
        html += `<option value="${escapeHtml(pk)}"${selected}>${escapeHtml(pk)} (${typeLabel})</option>`;
      }
      html += '</select>';
      if (taskKey === 'video_analyze' && eligibleProviders.length === 0 && providerKeys.length > 0) {
        html += '<span class="warn" style="font-size:var(--text-xs)">需要 capabilities 包含 video 的 Provider</span>';
      }
      html += '</div>';

      const hasProvider = currentProvider && providersObj[currentProvider];
      html += '<div class="task-binding-row">';
      html += '<label class="task-binding-label">模型</label>';
      if (hasProvider) {
        const models = providersObj[currentProvider].models || [];
        if (models.length > 0) {
          html += `<select class="task-model-select" data-task="${taskKey}">`;
          html += '<option value="">-- 选择模型 --</option>';
          for (const m of models) {
            const selected = m === currentModel ? ' selected' : '';
            html += `<option value="${escapeHtml(m)}"${selected}>${escapeHtml(m)}</option>`;
          }
          html += '</select>';
        } else {
          html += `<span class="warn">⚠️ 该 Provider 没有注册可用模型 <a href="#" class="edit-provider-link" data-provider="${escapeHtml(currentProvider)}" style="color:var(--accent);font-size:var(--text-xs)">编辑</a></span>`;
        }
      } else {
        html += '<span class="muted">请先选择 Provider</span>';
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
  let html = '<fieldset class="config-fieldset"><legend>AI 模型列表</legend>';

  if (keys.length === 0) {
    html += '<div class="config-empty-state"><p class="muted">还没有注册任何 AI 模型</p><p class="hint">点击下方按钮添加一个 AI 厂商（如 Gemini、DeepSeek、通义千问等），然后在 Project 标签页中为任务绑定模型。</p></div>';
  } else {
    html += '<p class="hint" style="margin:0 0 8px;line-height:1.6">在此管理 AI 厂商（Provider）。每个 Provider 需要：类型（决定能否做视频分析）、API 密钥（保存在 .env）、和模型列表（供任务绑定选择）。<br>添加完成后，切换到"项目"标签页为每个 AI 任务绑定具体的 Provider 和模型。</p>';
  }

  for (const name of keys) {
    const p = providersObj[name];
    const typeLabel = p.type === 'gemini' ? 'Gemini' : 'OpenAI 兼容';
    const hasModels = p.models && p.models.length > 0;
    const modelTags = hasModels
      ? p.models.map(m => `<span class="tag-chip">${escapeHtml(m)}</span>`).join(' ')
      : '<span class="warn">⚠️ 未注册模型</span>';
    const capabilityTags = providerCapabilities(p)
      .map(c => `<span class="tag-chip">${escapeHtml(c)}</span>`)
      .join(' ');

    html += `<div class="provider-card" data-provider="${escapeHtml(name)}">
      <div class="provider-card-header">
        <span class="provider-card-name">${escapeHtml(name)}</span>
        <span class="provider-card-type">${typeLabel}</span>
        <span class="provider-card-actions">
          <button class="btn-provider-test" data-provider="${escapeHtml(name)}">测试</button>
          <button class="btn-provider-edit" data-provider="${escapeHtml(name)}">编辑</button>
          ${DEFAULT_PROVIDERS.includes(name) ? '' : `<button class="btn-provider-delete" data-provider="${escapeHtml(name)}">删除</button>`}
        </span>
      </div>
      <div class="provider-card-body">
        <div class="provider-card-field"><span class="provider-card-label">类型</span><span>${typeLabel}</span></div>
        <div class="provider-card-field"><span class="provider-card-label">API 密钥</span><span class="provider-key-wrap" data-provider="${escapeHtml(name)}"><span class="provider-key-masked">••••••••••</span><span class="provider-key-value" style="display:none"></span><button class="btn-provider-show-key">显示</button></span></div>
        ${p.type !== 'gemini' ? `<div class="provider-card-field"><span class="provider-card-label">接口地址</span><span>${escapeHtml(p.base_url || '(默认)')}</span></div>` : ''}
        <div class="provider-card-field"><span class="provider-card-label">能力</span><span class="provider-card-models">${capabilityTags}</span></div>
        <div class="provider-card-field"><span class="provider-card-label">模型</span><span class="provider-card-models">${modelTags}</span></div>
        <div class="provider-card-field"><span class="provider-card-label">max_tokens</span><span>${p.max_tokens === 0 || p.max_tokens == null ? '不限制' : escapeHtml(String(p.max_tokens))}</span></div>
        <div class="provider-card-field"><span class="provider-card-label">timeout</span><span>${escapeHtml(String(p.timeout_sec ?? 120))}s</span></div>
        <div class="provider-test-status" data-provider="${escapeHtml(name)}"></div>
      </div>
    </div>`;
  }

  html += `<div style="margin-top:12px"><button id="btn-add-provider" class="btn-primary">${icon('plus', 14)} 添加 Provider</button></div>`;
  html += '</fieldset>';
  return html;
}

function _showProviderModal(providersObj, name, onSave) {
  const existing = name ? providersObj[name] : null;
  const isEdit = !!existing;
  const backdrop = document.createElement('div');
  backdrop.className = 'modal';
  backdrop.style.display = 'flex';

  const typeOptions = ['gemini', 'openai'].map(t =>
    `<option value="${t}"${existing?.type === t ? ' selected' : ''}>${t === 'gemini' ? 'Gemini（支持视频分析）' : 'OpenAI 兼容（纯文本，如 DeepSeek）'}</option>`
  ).join('');

  backdrop.innerHTML = `
    <div class="modal-backdrop"></div>
    <div class="modal-dialog" style="max-width:520px">
      <h3 style="margin:0 0 var(--space-4)">${isEdit ? '编辑 Provider' : '添加 Provider'}</h3>
      <div class="form-group">
        <label class="form-label">名称 <span class="muted">（唯一标识符）</span></label>
        <input id="modal-provider-name" class="form-input" value="${escapeHtml(name || '')}" ${isEdit ? 'readonly' : ''} placeholder="如 my-gemini">
      </div>
      <div class="form-group">
        <label class="form-label">类型</label>
        <select id="modal-provider-type" class="form-input">${typeOptions}</select>
        <span class="hint">Gemini 支持视频分析和纯文本任务；OpenAI 兼容（如 DeepSeek、通义千问）仅支持文本任务</span>
      </div>
      <div class="form-group">
        <label class="form-label">API 密钥</label>
        <div style="display:flex;gap:8px">
          <input id="modal-provider-key" class="form-input" type="password" placeholder="输入 API 密钥" style="flex:1">
          <button id="modal-key-toggle" class="btn-secondary">显示</button>
        </div>
        <span class="hint">密钥将通过 .env 文件存储，不会写入 config.yaml${isEdit ? '。留空则保持现有密钥不变' : ''}</span>
      </div>
      <div class="form-group" id="modal-base-url-group" style="${existing?.type === 'gemini' && !isEdit ? 'display:none' : ''}">
        <label class="form-label">接口地址</label>
        <input id="modal-provider-base-url" class="form-input" value="${escapeHtml(existing?.base_url || '')}" placeholder="https://api.openai.com/v1">
        <span class="hint">仅 OpenAI 兼容类型需要填写</span>
      </div>
      <div class="form-group">
        <label class="form-label">模型列表 <span class="muted">（按回车添加）</span></label>
        <div id="modal-provider-models"></div>
        <span class="hint">输入该厂商可用的模型名称。默认厂商已预填常用模型。这些模型将出现在项目标签页的任务绑定下拉菜单中。</span>
      </div>
      <div class="form-group">
        <label class="form-label">能力标签 <span class="muted">（按回车添加）</span></label>
        <div id="modal-provider-capabilities"></div>
        <span class="hint">常用标签：video 表示可做视频理解，text 表示可做文本生成。</span>
      </div>
      <div class="form-group" style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div>
          <label class="form-label">max_tokens</label>
          <input id="modal-provider-max-tokens" class="form-input" type="number" min="0" step="1"
            value="${existing?.max_tokens ?? 0}">
          <span class="hint">0 = 不限制（推荐规划任务）</span>
        </div>
        <div>
          <label class="form-label">timeout_sec</label>
          <input id="modal-provider-timeout" class="form-input" type="number" min="0" step="1"
            value="${existing?.timeout_sec ?? 120}">
          <span class="hint">HTTP 超时（秒）</span>
        </div>
        <div>
          <label class="form-label">retry_attempts</label>
          <input id="modal-provider-retry" class="form-input" type="number" min="0" step="1"
            value="${existing?.retry_attempts ?? 2}">
          <span class="hint">额外重试次数</span>
        </div>
        <div>
          <label class="form-label">requests_per_minute</label>
          <input id="modal-provider-rpm" class="form-input" type="number" min="0" step="1"
            value="${existing?.requests_per_minute ?? 0}">
          <span class="hint">0 = 不限速</span>
        </div>
        <div id="modal-poll-group" style="${(existing?.type || 'gemini') === 'gemini' ? '' : 'display:none'}">
          <label class="form-label">poll_interval_sec</label>
          <input id="modal-provider-poll" class="form-input" type="number" min="0" step="1"
            value="${existing?.poll_interval_sec ?? 5}">
          <span class="hint">Gemini 文件轮询间隔</span>
        </div>
      </div>
      <div class="modal-actions">
        <button id="modal-cancel" class="btn-secondary">取消</button>
        <button id="modal-save" class="btn-primary">${isEdit ? '保存修改' : '添加'}</button>
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
    backdrop.querySelector('#modal-key-toggle').textContent = isPwd ? '隐藏' : '显示';
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

    if (!newName) { setStatus('请填写 Provider 名称', 'warn'); return; }
    if (isEdit && newName !== name) { setStatus('名称不可修改（如需重命名请删除后重建）', 'warn'); return; }
    if (!isEdit && providersObj[newName]) { setStatus(`Provider "${newName}" 已存在`, 'warn'); return; }
    if (newMaxTokens < 0 || newTimeout < 0 || newRetry < 0 || newRpm < 0 || newPoll < 0) {
      setStatus('数值字段不能为负数', 'warn');
      return;
    }

    // Save API key to .env if provided
    if (newKey) {
      const envNameEnv = newName.toUpperCase() + '_API_KEY';
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
        if (!envResult.ok) { setStatus('API 密钥保存失败: ' + (envResult.error || ''), 'err'); return; }
      } catch (e) { setStatus('API 密钥保存失败: ' + e.message, 'err'); return; }
    }

    const envName = newName.toUpperCase() + '_API_KEY';
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
  if (models.length === 0) return { ok: false, error: '请先添加模型' };
  if (models.length === 1) return { ok: true, model: models[0] };

  const selected = prompt(`请选择要测试的模型：\n${models.join('\n')}`, models[0]);
  if (selected === null) return { ok: false, error: '已取消测试', canceled: true };
  const model = selected.trim();
  if (!models.includes(model)) return { ok: false, error: '请选择有效模型' };
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
        status.textContent = '测试中...';
      }
      try {
        const result = await _testProvider(providersObj, providerName);
        if (result?.canceled) {
          if (status) status.textContent = result.error || '已取消测试';
          return;
        }
        if (result?.ok) {
          if (status) {
            status.className = 'provider-test-status ok';
            status.textContent = `测试成功：${result.elapsed_ms ?? '?'} ms`;
          }
        } else {
          if (status) {
            status.className = 'provider-test-status err';
            status.textContent = '测试失败：' + (result?.error || '未知错误');
          }
        }
      } catch (e) {
        if (status) {
          status.className = 'provider-test-status err';
          status.textContent = '测试失败：' + (e.message || e);
        }
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
      const tasks = state.configProject?.ai?.tasks || {};
      const refs = Object.entries(tasks)
        .filter(([_, t]) => t.provider === name)
        .map(([k]) => k);
      if (refs.length > 0) {
        if (!confirm(`以下任务正在使用 "${name}"：${refs.join('、')}。确定删除？删除后这些任务将无法运行。`)) return;
      } else {
        if (!confirm(`确定删除 Provider "${name}"？`)) return;
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
        btn.textContent = '显示';
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
            if (!found) { setStatus(`未找到 ${envName}`, 'warn'); return; }
          } catch {
            setStatus('无法读取 .env 文件', 'err'); return;
          }
        }
        value.style.display = '';
        masked.style.display = 'none';
        btn.textContent = '隐藏';
      }
    };
  });
}

function _attachTaskBindingHandlers(pane, projectCfg) {
  const reRender = () => renderConfig();

  pane.querySelectorAll('.task-provider-select').forEach(sel => {
    sel.onchange = () => {
      const taskKey = sel.dataset.task;
      setDeep(projectCfg, `ai.tasks.${taskKey}.provider`, sel.value);
      setDeep(projectCfg, `ai.tasks.${taskKey}.model`, '');
      markDirty();
      reRender();
    };
  });

  pane.querySelectorAll('.task-model-select').forEach(sel => {
    sel.onchange = () => {
      const taskKey = sel.dataset.task;
      setDeep(projectCfg, `ai.tasks.${taskKey}.model`, sel.value);
      markDirty();
    };
  });

  pane.querySelectorAll('.refine-follow-check').forEach(cb => {
    cb.onchange = () => {
      if (cb.checked) {
        if (projectCfg.ai?.tasks?.refine_text) {
          delete projectCfg.ai.tasks.refine_text;
        }
      } else {
        if (!projectCfg.ai) projectCfg.ai = {};
        if (!projectCfg.ai.tasks) projectCfg.ai.tasks = {};
        projectCfg.ai.tasks.refine_text = { provider: '', model: '' };
      }
      markDirty();
      reRender();
    };
  });

  const gotoBtn = pane.querySelector('#goto-global-providers');
  if (gotoBtn) {
    gotoBtn.onclick = (e) => {
      e.preventDefault();
      if (state.dirty && !confirm('设置有未保存的修改，确定切换标签吗？')) return;
      state.configTab = 'global';
      clearDirty();
      renderConfig();
    };
  }

  pane.querySelectorAll('.edit-provider-link').forEach(link => {
    link.onclick = (e) => {
      e.preventDefault();
      if (state.dirty && !confirm('设置有未保存的修改，确定切换标签吗？')) return;
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
  return `<div class="config-fallback-warn" style="background:var(--warning-bg,#fff3cd);border:1px solid var(--warning-border,#ffc107);border-radius:6px;padding:10px 14px;margin-bottom:12px;display:flex;align-items:flex-start;gap:8px;font-size:var(--text-sm);color:var(--text-primary)">
    <span style="font-size:18px;line-height:1">⚠️</span>
    <span>当前显示的是全局配置（回退）。该项目没有专属 <code>project.yaml</code>，修改将影响所有项目。建议<a href="#" id="link-create-project-config" style="text-decoration:underline;color:var(--accent)">创建专属配置</a>。</span>
  </div>`;
}

function _renderEnvEditor() {
  return `<div style="margin-bottom:16px;border-bottom:1px solid var(--border);padding-bottom:12px">
    <button id="btn-env-toggle" class="btn-secondary" style="font-size:var(--text-sm)">${icon('file-text', 14)} 编辑 .env 文件</button>
    <div id="env-editor" style="display:none;margin-top:8px">
      <textarea id="env-textarea" style="width:100%;min-height:160px;font-family:var(--font-mono,monospace);font-size:var(--text-xs,12px);padding:8px;background:var(--bg-surface);color:var(--text-primary);border:1px solid var(--border);border-radius:var(--radius-sm)" spellcheck="false"></textarea>
      <div style="display:flex;gap:8px;margin-top:6px">
        <button id="btn-env-save" class="btn-primary" style="font-size:var(--text-sm)">${icon('check', 14)} 保存 .env</button>
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
      envToggle.innerHTML = visible ? `${icon('file-text', 14)} 编辑 .env 文件` : `${icon('x', 14)} 收起`;
      if (!visible && !envData.content) {
        try {
          envData = await api('GET', '/api/env');
          envTextarea.value = envData.content || '';
        } catch (e) {
          envMsg.textContent = '加载失败';
        }
      }
    };
  }
  if (envSave && envTextarea && envMsg) {
    envSave.onclick = async () => {
      envMsg.textContent = '保存中...';
      try {
        const r = await api('PUT', '/api/env', { content: envTextarea.value });
        if (r.ok) {
          envMsg.textContent = `✓ 已保存到 ${r.path}`;
          envData.content = envTextarea.value;
        } else {
          envMsg.textContent = `✗ ${r.error || '保存失败'}`;
        }
      } catch (e) {
        envMsg.textContent = `✗ 保存失败: ${e.message || e}`;
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
    btn.innerHTML = `${icon('plus', 14)} 添加默认模板`;
    btn.onclick = () => {
      const template = '## 项目背景\n- 拍摄地点：[填写拍摄地点]\n- 行程安排：[填写行程安排]\n- 人物/事件：[填写人物或事件]\n- 注意事项：[填写注意事项]\n\n请根据以上信息调整 AI 的分析和口播生成方向。';
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
  if (state._needsConfigInit) {
    pane.innerHTML = `
      <h3>项目配置初始化</h3>
      <p class="muted">该项目还没有专属配置文件。</p>
      <p class="hint">创建后将以当前全局配置为模板，后续修改只影响本项目。</p>
      <button id="btn-config-init" class="btn-primary" style="margin-top:12px">${icon('settings', 16)} 为该项目创建配置文件</button>
    `;
    const btn = $('btn-config-init');
    if (btn) btn.onclick = initProjectConfig;
    return;
  }
  if (!state.configRaw || Object.keys(state.configRaw).length === 0) {
    pane.innerHTML = '<p class="muted">配置数据不可用</p>';
    return;
  }

  _ensureDefaultProviderModels();

  const isFallback = state.configRaw._config_source === 'global_fallback';
  const active = state.configTab || 'project';
  const descs = state.configRaw._descriptions || {};

  let contentHtml = '';
  if (active === 'project') {
    const projectData = state.configProject || {};
    contentHtml = `<div class="config-form">${_renderConfigProject(projectData, state.configGlobal, descs)}</div>`;
  } else if (active === 'global') {
    const globalData = state.configGlobal || {};
    contentHtml = _renderEnvEditor()
      + `<div class="config-form">${_renderConfigGlobal(globalData, descs)}</div>`;
  } else if (active === 'prompts') {
    contentHtml = '<div id="prompt-management-root"></div>';
  } else {
    // Merged tab: read-only merged view
    const { _config_source, _needsConfigInit, _descriptions, ...configData } = state.configRaw;
    contentHtml = `<p class="hint" style="margin-bottom:8px">以下为全局 + 项目各层合并后的有效配置。只读。</p>
      <div class="config-form config-merged">${_renderConfigForm(configData, '', descs)}</div>`;
  }

  pane.innerHTML = `
    <div class="config-tab-bar">
      ${_tabBtn('项目', 'project', active === 'project')}
      ${_tabBtn('全局', 'global', active === 'global')}
      ${_tabBtn('Prompts', 'prompts', active === 'prompts')}
      ${_tabBtn('合并视图', 'merged', active === 'merged')}
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
      if (state.dirty && !confirm('设置有未保存的修改，确定切换标签吗？')) return;
      state.configTab = btn.dataset.configTab;
      clearDirty();
      renderConfig();
    };
  });

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
  } else if (active === 'project') {
    _attachConfigForm(pane, state.configProject || {}, descs);
    _attachContextTemplate(pane);
    _attachTaskBindingHandlers(pane, state.configProject);
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
        <span class="config-card-title">Whisper 模型管理</span>
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
/** Skip overlapping /api/logs polls (P2-P38). */
let _logsPollInFlight = false;

const _LEVEL_LABELS = {
  debug: '调试',
  info: '信息',
  warn: '警告',
  error: '错误',
};

const _SINCE_CHIPS = [
  { ms: 0, label: '全部' },
  { ms: 5 * 60 * 1000, label: '5分钟' },
  { ms: 15 * 60 * 1000, label: '15分钟' },
  { ms: 60 * 60 * 1000, label: '60分钟' },
];

function _logsFilterOpts() {
  return {
    query: _logsFilterQuery,
    level: _logsFilterLevel,
    sinceMs: _logsFilterSinceMs || undefined,
  };
}

function _appendLogLineEl(view, entryOrLine) {
  const entry = normalizeLogEntry(entryOrLine);
  const level = inferLogLevel(entry.text);
  const d = document.createElement('div');
  d.className = `log-line ${logLineClass(entry)}`;
  d.dataset.level = level;
  const badge = document.createElement('span');
  badge.className = 'log-level-badge';
  badge.textContent = _LEVEL_LABELS[level] || '信息';
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
  const visible = filterLogEntries(_logsBuffer, _logsFilterOpts());
  view.innerHTML = '';
  for (const entry of visible) {
    _appendLogLineEl(view, entry);
  }
  const empty = $('logs-empty-hint');
  if (empty) {
    const filteredOut = _logsBuffer.length > 0 && visible.length === 0;
    empty.hidden = !filteredOut;
  }
  const countEl = $('logs-match-count');
  if (countEl) {
    countEl.textContent = _logsBuffer.length
      ? `显示 ${visible.length} / ${_logsBuffer.length}`
      : '';
  }
  if (_logsAutoScroll) view.scrollTop = view.scrollHeight;
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
  _logsPollInFlight = false;
}

export function renderLogs() {
  const pane = $('tab-logs');
  stopLogsPolling();
  const sinceChipsHtml = _SINCE_CHIPS.map((c) =>
    `<button type="button" class="logs-chip" data-log-since="${c.ms}">${c.label}</button>`
  ).join('');
  pane.innerHTML = `
    <div class="logs-toolbar">
      <span class="logs-toolbar-title">会话日志</span>
      <input type="search" id="logs-filter-query" class="logs-filter-input"
        placeholder="过滤关键字…" autocomplete="off"
        value="${escapeHtml(_logsFilterQuery)}"
        aria-label="日志关键字过滤">
      <div class="logs-level-chips" role="group" aria-label="日志等级">
        <button type="button" class="logs-chip" data-log-level="all">全部</button>
        <button type="button" class="logs-chip" data-log-level="info">信息</button>
        <button type="button" class="logs-chip" data-log-level="warn">警告</button>
        <button type="button" class="logs-chip" data-log-level="error">错误</button>
      </div>
      <div class="logs-since-chips" role="group" aria-label="时间范围">
        ${sinceChipsHtml}
      </div>
      <span id="logs-match-count" class="logs-match-count muted"></span>
      <label class="logs-autoscroll-label">
        <input type="checkbox" id="logs-autoscroll" ${_logsAutoScroll ? 'checked' : ''}> 自动滚动
      </label>
      <button type="button" class="btn-secondary" id="btn-logs-clear">清空</button>
    </div>
    <p id="logs-empty-hint" class="logs-empty-hint muted" hidden>没有匹配当前过滤条件的日志</p>
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
    try {
      await api('POST', '/api/logs/clear', {});
      _logsBuffer = [];
      _logsOffset = 0;
      _paintLogsView(view);
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
      countEl.textContent = `显示 ${visible.length} / ${_logsBuffer.length}`;
    }
    if (_logsAutoScroll) view.scrollTop = view.scrollHeight;
  };

  const pollOnce = async () => {
    if (_logsPollInFlight) return;
    _logsPollInFlight = true;
    try {
      const r = await api('GET', `/api/logs?offset=${_logsOffset}`);
      if (!r || !r.logs) return;
      // Left the logs pane while in flight — do not touch detached DOM.
      if (!$('logs-view')) return;
      ingest(r.logs);
      if (typeof r.total === 'number') _logsOffset = r.total;
    } catch { /* ignore */ } finally {
      _logsPollInFlight = false;
    }
  };

  // Re-enter: show retained buffer immediately, then resume from offset.
  _paintLogsView(view);
  _logsTimer = setInterval(pollOnce, 2000);
  pollOnce();
}


export async function renderTokens() {
  const pane = $('tab-tokens');
  pane.innerHTML = '<p class="muted">加载中...</p>';
  try {
    const data = await api('GET', '/api/token-usage');
    if (!data || !data.total) {
      pane.innerHTML = '<p class="muted">暂无 token 使用数据。运行流水线后会自动记录。</p>';
      return;
    }
    const t = data.total;
    const totalHtml = `
      <div style="display:flex;gap:var(--space-3);margin-bottom:var(--space-3);flex-wrap:wrap">
        <div class="token-card"><div class="token-card-value">${t.total_tokens.toLocaleString()}</div><div class="token-card-label">总 Token</div></div>
        <div class="token-card"><div class="token-card-value">${t.prompt_tokens.toLocaleString()}</div><div class="token-card-label">Prompt</div></div>
        <div class="token-card"><div class="token-card-value">${t.completion_tokens.toLocaleString()}</div><div class="token-card-label">Completion</div></div>
      </div>`;

    let modelHtml = '<h4 style="margin:var(--space-2) 0">按模型</h4><table class="token-table"><tr><th>模型</th><th>调用次数</th><th>Prompt</th><th>Completion</th><th>总计</th></tr>';
    for (const [model, m] of Object.entries(data.by_model || {})) {
      modelHtml += `<tr><td>${escapeHtml(model)}</td><td>${m.calls}</td><td>${m.prompt_tokens.toLocaleString()}</td><td>${m.completion_tokens.toLocaleString()}</td><td>${m.total_tokens.toLocaleString()}</td></tr>`;
    }
    modelHtml += '</table>';

    let taskHtml = '<h4 style="margin:var(--space-2) 0">按任务</h4><table class="token-table"><tr><th>任务</th><th>调用次数</th><th>Prompt</th><th>Completion</th><th>总计</th></tr>';
    for (const [task, tk] of Object.entries(data.by_task || {})) {
      taskHtml += `<tr><td>${escapeHtml(task)}</td><td>${tk.calls}</td><td>${tk.prompt_tokens.toLocaleString()}</td><td>${tk.completion_tokens.toLocaleString()}</td><td>${tk.total_tokens.toLocaleString()}</td></tr>`;
    }
    taskHtml += '</table>';

    let historyHtml = '<h4 style="margin:var(--space-2) 0">历史记录</h4><table class="token-table"><tr><th>时间</th><th>任务</th><th>模型</th><th>Prompt</th><th>Completion</th><th>总计</th></tr>';
    for (const h of (data.history || []).slice().reverse().slice(0, 100)) {
      historyHtml += `<tr><td class="token-time">${escapeHtml(h.timestamp || '')}</td><td>${escapeHtml(h.task || '')}</td><td>${escapeHtml(h.model || '')}</td><td>${(h.prompt_tokens || 0).toLocaleString()}</td><td>${(h.completion_tokens || 0).toLocaleString()}</td><td>${(h.total_tokens || 0).toLocaleString()}</td></tr>`;
    }
    historyHtml += '</table>';

    pane.innerHTML = `<div style="padding:var(--space-2)">${totalHtml}${modelHtml}${taskHtml}${historyHtml}</div>`;
  } catch (e) {
    pane.innerHTML = `<p class="muted">加载失败: ${escapeHtml(e.message || e)}</p>`;
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
  if (msg) msg.textContent = event?.message || task.message || task.phase || '下载中...';
  if (cancelBtn) cancelBtn.style.display = task.status === 'queued' || task.status === 'running' || task.status === 'cancelling' ? '' : 'none';
  if (dlBtn && (task.status === 'queued' || task.status === 'running' || task.status === 'cancelling')) {
    dlBtn.disabled = true;
    dlBtn.textContent = task.status === 'cancelling' ? '取消中...' : '下载中...';
  }
  if (!['succeeded', 'failed', 'cancelled', 'interrupted'].includes(task.status)) return;
  if (_installPollTimer) { clearInterval(_installPollTimer); _installPollTimer = null; }
  _clearWhisperTaskSubscription();
  if (dlBtn) { dlBtn.disabled = false; dlBtn.innerHTML = `${icon('download', 14)} 下载模型`; }
  if (task.status === 'succeeded') {
    setStatus('Whisper 模型下载完成', 'ok');
    _loadModelMgmt();
  } else if (task.status === 'cancelled') {
    setStatus('Whisper 模型下载已取消', 'warn');
  } else {
    setStatus(`Whisper 模型下载失败: ${task.error_message || task.message || '未知错误'}`, 'err');
  }
}

function renderModelManagement() {
  const div = document.createElement('div');
  div.id = 'whisper-model-mgmt';
  div.style.cssText = 'padding:0;background:transparent;border:none;border-radius:0';
  div.innerHTML = `
    <div id="model-mgmt-content">
      <p class="muted">加载中...</p>
    </div>
  `;
  return div;
}


async function _loadModelMgmt() {
  const container = $('model-mgmt-content');
  if (!container) {
    setStatus('模型管理: DOM 未就绪', 'warn');
    return;
  }
  const timeoutId = setTimeout(() => {
    if (container && container.querySelector('.muted')) {
      container.innerHTML = '<p class="err">请求超时 — 请确认后端服务运行正常。</p>';
    }
  }, 10000);
  try {
    const [data, check] = await Promise.all([
      api('GET', '/api/whisper/models'),
      api('GET', '/api/whisper/check').catch(() => ({ cublas: true })),
    ]);
    if (!data.ok) { container.innerHTML = '<p class="err">加载模型列表失败</p>'; return; }

    const current = data.current_model || 'medium';
    const avail = data.available || [];
    const cached = data.cached || [];
    const cublasOk = check.cublas !== false;
    const frozen = check.frozen === true;

    let html = '<div style="display:flex;gap:12px;flex-wrap:wrap;align-items:end">';

    html += '<div style="flex:1;min-width:140px">';
    html += '<label style="font-size:var(--text-xs);color:var(--text-secondary)">当前模型</label>';
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
      btnText = '打包版不支持安装';
      btnDisabled = ' disabled';
    } else {
      btnText = needsReverify ? '重新验证' : alreadyCached ? '已下载' : '下载模型';
      btnDisabled = alreadyCached && !needsReverify ? ' disabled' : '';
    }
    html += `<button id="btn-model-download" class="btn-primary" style="font-size:var(--text-sm)"${btnDisabled}>${icon('download', 14)} ${btnText}</button>`;
    if (needsReverify) {
      html += '<p style="font-size:var(--text-xs);color:var(--warn,#b8860b);margin:4px 0 0">模型文件已缓存，但 cuBLAS 环境验证未通过。点击「重新验证」重试。</p>';
    }
    if (frozen) {
      html += '<p style="font-size:var(--text-xs);color:var(--warn,#b8860b);margin:4px 0 0">faster-whisper（约 4 GB）未打包进 clio.exe。如需转录，请改用源码版运行 <code>python main.py whisper install</code>。</p>';
    }
    html += '</div>';

    html += '<div style="font-size:var(--text-xs);color:var(--text-secondary);white-space:nowrap">';
    html += `可用空间: ${escapeHtml(data.free_display || '?')}`;
    html += '</div>';

    html += '</div>';

    if (cached.length) {
      html += '<div style="margin-top:8px">';
      html += '<p style="font-size:var(--text-xs);color:var(--text-secondary);margin:0 0 4px">已缓存模型:</p>';
      for (const m of cached) {
        const validCls = m.valid ? 'ok' : 'err';
        html += '<div style="display:flex;gap:8px;align-items:center;padding:4px 0;font-size:var(--text-xs)">';
        html += `<span style="flex:1">${escapeHtml(m.name)} <span class="${validCls}">${m.valid ? '✓' : '✗ (不完整)'}</span> <span class="muted">${escapeHtml(m.size_display)}</span></span>`;
        html += `<button class="btn-delete-model" data-model="${escapeHtml(m.name)}" style="background:none;border:1px solid var(--err,#c44);color:var(--err,#c44);padding:2px 8px;border-radius:3px;cursor:pointer;font-size:var(--text-xs)">删除</button>`;
        html += '</div>';
      }
      html += '</div>';
    } else {
      html += '<p class="muted" style="margin-top:8px;font-size:var(--text-xs)">尚未缓存任何模型。请先下载。</p>';
    }

    html += '<div id="model-dl-progress" style="display:none;margin-top:8px">';
    html += '<div style="display:flex;justify-content:space-between;font-size:var(--text-xs);margin-bottom:4px">';
    html += '<span id="model-dl-msg"></span><span id="model-dl-speed"></span></div>';
    html += '<div style="background:#333;border-radius:3px;height:6px;overflow:hidden">';
    html += '<div id="model-dl-bar" style="background:var(--accent);border-radius:3px;height:100%;width:0%"></div></div>';
    html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-top:2px">';
    html += '<p id="model-dl-eta" class="muted" style="font-size:var(--text-xs);margin:0"></p>';
    html += '<button id="btn-cancel-dl" style="background:none;border:1px solid var(--err,#c44);color:var(--err,#c44);padding:2px 10px;border-radius:3px;cursor:pointer;font-size:var(--text-xs);display:none">取消下载</button>';
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
          dlBtn.innerHTML = isCached ? (needsReverify ? '重新验证' : '已下载') : `${icon('download', 14)} 下载模型`;
        }
        try {
          const r = await api('PUT', '/api/whisper/model', { model_size: newModel });
          if (r.ok) {
            setStatus(`模型已切换为 ${newModel}`, 'ok');
          } else {
            setStatus('切换模型失败: ' + (r.error || ''), 'err');
            sel.value = current;
          }
        } catch (e) {
          setStatus('切换模型失败: ' + e.message, 'err');
          sel.value = current;
        }
      };
    }

    const dlBtn = $('btn-model-download');
    if (dlBtn) {
      dlBtn.onclick = async () => {
        if (check.frozen === true) {
          setStatus('打包版不支持在此安装 Whisper，请使用源码版运行 python main.py whisper install', 'warn');
          return;
        }
        dlBtn.disabled = true;
        dlBtn.textContent = '启动下载...';
        const prog = $('model-dl-progress');
        if (prog) prog.style.display = 'block';
        const cancelBtn = $('btn-cancel-dl');
        if (cancelBtn) cancelBtn.style.display = '';
        try {
          const r = await api('POST', '/api/whisper/install', {});
          if (!r.ok) throw new Error(r.error || '启动失败');
          dlBtn.textContent = '下载中...';
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
          dlBtn.innerHTML = `${icon('download', 14)} 下载模型`;
          const progMsg = $('model-dl-msg');
          if (progMsg) progMsg.textContent = '启动失败: ' + e.message;
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
          cancelBtn.textContent = '取消中...';
          return;
        }
        if (_installPollTimer) { clearInterval(_installPollTimer); _installPollTimer = null; }
        const prog = $('model-dl-progress');
        if (prog) prog.style.display = 'none';
        cancelBtn.style.display = 'none';
        const dlBtn = $('btn-model-download');
        if (dlBtn) { dlBtn.disabled = false; dlBtn.innerHTML = `${icon('download', 14)} 下载模型`; }
      };
    }

    container.querySelectorAll('.btn-delete-model').forEach(btn => {
      btn.onclick = async () => {
        const modelName = btn.dataset.model;
        if (!confirm(`确定删除模型 ${modelName}？将释放磁盘空间。`)) return;
        try {
          const r = await api('POST', '/api/whisper/models/delete', { name: modelName });
          if (r.ok) {
            setStatus(`模型 ${modelName} 已删除`, 'ok');
            _loadModelMgmt();
          } else {
            setStatus('删除失败: ' + (r.error || ''), 'err');
          }
        } catch (e) {
          setStatus('删除失败: ' + e.message, 'err');
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
        if (dlBtn) { dlBtn.disabled = true; dlBtn.textContent = '下载中...'; }
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
    if (container) container.innerHTML = `<p class="err">加载失败: ${escapeHtml(e.message)}</p>`;
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
      if (msg) msg.textContent = s.message || '下载中...';
      if (speed && s.speed) speed.textContent = s.speed;
      if (eta) {
        if (s.eta_sec != null) {
          const m = Math.floor(s.eta_sec / 60);
          const sec = s.eta_sec % 60;
          eta.textContent = `预计剩余 ${m} 分 ${sec} 秒`;
        } else { eta.textContent = ''; }
      }
    } else if (s.status === 'done') {
      if (_installPollTimer) { clearInterval(_installPollTimer); _installPollTimer = null; }
      if (cancelBtn) cancelBtn.style.display = 'none';
      if (bar) bar.style.width = '100%';
      if (msg) msg.textContent = '✔ 下载完成';
      if (eta) eta.textContent = '';
      if (dlBtn) { dlBtn.disabled = false; dlBtn.innerHTML = `${icon('download', 14)} 下载模型`; }
      _loadModelMgmt();
    } else if (s.status === 'error') {
      if (_installPollTimer) { clearInterval(_installPollTimer); _installPollTimer = null; }
      if (cancelBtn) cancelBtn.style.display = 'none';
      if (dlBtn) { dlBtn.disabled = false; dlBtn.innerHTML = `${icon('download', 14)} 重试下载`; }
      if (msg) msg.textContent = s.message || '下载失败';
    }
  } catch { /* ignore */ }
}
