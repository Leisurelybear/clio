import { state } from './state.js';
import {
  $,
  escapeHtml,
  setStatus,
  updateSidebarDay,
} from './utils.js';
import { api, icon } from './api.js';
import { beginLatest, endLatest, isLatest, isAbortError } from './latest.js';
import { addToast } from './toast.js';

let _runEventSource = null;
let _lastRunDay = 'day1';
let _runActive = false;
let _lastProgressSnapshot = null;
let _lastRunSteps = [];
let _expectDoneNavigation = false;
let _seenNonTerminal = false;

const STEPS_KEY = 'vlog_ui_run_steps';

const RUN_STEPS = [
  { key: 'compress', label: '压缩原视频', hint: '将原片压缩为 640p，为 AI 分析做准备' },
  { key: 'analyze', label: 'AI 分析', hint: '提交 Gemini 分析压缩后的视频内容' },
  { key: 'voiceover', label: '生成口播文案', hint: '基于分析结果生成每段的口播脚本' },
  { key: 'transcribe', label: 'Whisper 语音转录', hint: '用 faster-whisper 转录音频为文字（需安装）' },
  { key: 'plan', label: 'vlog 剪辑规划', hint: '根据所有素材生成剪辑顺序和时间轴' },
  { key: 'label', label: '烧录序号', hint: '在压缩视频左上角标上序号便于剪映对照' },
];

const FFMPEG_RUN_STEPS = new Set(['compress', 'label', 'transcribe']);

/** True if any selected step hard-requires ffmpeg/ffprobe. */
export function mediaStepsNeedFfmpeg(steps) {
  return (Array.isArray(steps) ? steps : []).some((s) => FFMPEG_RUN_STEPS.has(s));
}

function loadStepSelection() {
  try {
    const raw = localStorage.getItem(STEPS_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

function saveStepSelection(checks, useTranscripts) {
  try {
    localStorage.setItem(STEPS_KEY, JSON.stringify({ steps: checks, use_transcripts: useTranscripts }));
  } catch { /* ignore */ }
}

function renderRun() {
  if (_shouldResetRunNavigationOnRender(_runActive)) {
    _expectDoneNavigation = false;
    _seenNonTerminal = false;
  }
  _lastRunDay = state.currentDay || 'day1';
  const pane = $('tab-run');
  const saved = loadStepSelection();
  const savedSteps = saved.steps || {};
  const savedUseTrans = saved.use_transcripts !== false;

  const stepChecks = RUN_STEPS.map(s => {
    const checked = savedSteps[s.key] !== false;
    const isPlan = s.key === 'plan';
    return `
      <div class="run-step-wrap">
        <label class="run-step ${isPlan ? 'run-step-plan' : ''}">
          <input type="checkbox" class="run-step-cb" data-step="${s.key}" ${checked ? 'checked' : ''}>
          <span class="run-step-label">${s.label}</span>
          <span class="run-step-hint">${s.hint}</span>
        </label>
        ${isPlan ? `
        <div class="run-step-sub">
          <label class="run-option">
            <span class="run-option-label">分集</span>
            <input id="run-day" class="run-option-input" value="${escapeHtml(state.currentDay)}">
          </label>
          <label class="run-option run-option-check">
            <input type="checkbox" id="run-use-transcripts" ${savedUseTrans ? 'checked' : ''}>
            <span>使用语音转录优化剪辑规划</span>
          </label>
        </div>
        ` : ''}
      </div>
    `;
  }).join('');

  pane.innerHTML = `
    <h3>运行流水线</h3>
    <p class="hint">选择要执行的步骤后点击「运行选中步骤」</p>
    <p class="hint">当前项目：${escapeHtml(state.currentProjectDir || state.config?.project_dir || '未设置')}</p>
    <div class="run-step-list">${stepChecks}</div>
    <details class="run-prompt-section" style="margin:12px 0">
      <summary style="cursor:pointer;font-size:var(--text-sm);color:var(--text-secondary);user-select:none">⌨ 高级提示词（可选）</summary>
      <div style="margin-top:8px">
        <textarea id="run-context-override" class="run-prompt-input" placeholder="在本次运行时临时向所有 AI 添加额外指令。&#10;&#10;每条指令一行，支持按步骤前缀:&#10;[analyze] 注意画面中的食物特写&#10;[voiceover] 使用更口语化的风格&#10;[plan] 优先选取运动镜头&#10;&#10;不带前缀的指令将应用于所有步骤。&#10;这些提示仅在本次运行有效，不会保存到配置中。" rows="4" style="width:100%;box-sizing:border-box;padding:8px;border:1px solid var(--border);border-radius:4px;background:var(--bg-input,#1e1e1e);color:var(--text-primary);font-size:var(--text-sm);resize:vertical;font-family:inherit"></textarea>
      </div>
    </details>
    <div style="display:flex;gap:8px;align-items:center;margin-top:12px;flex-wrap:wrap">
      <button id="btn-run-start" class="btn-primary">${getRunButtonText()}</button>
      <span id="run-files-badge" class="run-files-badge" style="display:none"></span>
      <button id="btn-run-cancel" class="btn-secondary" style="display:none">取消</button>
      <label class="run-option-check" id="option-overwrite-wrap">
        <input type="checkbox" id="run-overwrite">
        <span>覆盖现有输出</span>
      </label>
    </div>
    <div id="run-preview" style="margin-top:12px"></div>
    <div id="run-progress" style="margin-top:12px"></div>
    <div id="run-state-container"></div>
  `;

  // wire step checkbox change → persist + refresh preview
  document.querySelectorAll('.run-step-cb').forEach(cb => {
    cb.addEventListener('change', () => {
      const checks = {};
      document.querySelectorAll('.run-step-cb').forEach(c => {
        checks[c.dataset.step] = c.checked;
      });
      saveStepSelection(checks, $('run-use-transcripts')?.checked ?? true);
      togglePlanSubOptions();
      updateRunStartButtonState();
      refreshRunPreview({ silent: true });
    });
  });
  // wire use_transcripts change → persist + preview
  const useTransCb = $('run-use-transcripts');
  if (useTransCb) {
    useTransCb.addEventListener('change', () => {
      const checks = {};
      document.querySelectorAll('.run-step-cb').forEach(c => {
        checks[c.dataset.step] = c.checked;
      });
      saveStepSelection(checks, useTransCb.checked);
      refreshRunPreview({ silent: true });
    });
  }
  const overwriteCb = $('run-overwrite');
  if (overwriteCb) {
    overwriteCb.addEventListener('change', () => refreshRunPreview({ silent: true }));
  }

  togglePlanSubOptions();
  updateRunFilesBadge();
  updateRunStartButtonState();
  refreshRunPreview({ silent: true });

  const runBtn = $('btn-run-start');
  runBtn.onclick = startRun;
  if (_runActive) { runBtn.disabled = true; runBtn.textContent = '运行中...'; }
  const cancelBtn = $('btn-run-cancel');
  if (cancelBtn) cancelBtn.onclick = cancelRun;
  _startRunSSE();
}

function _shouldResetRunNavigationOnRender(runActive) {
  return !runActive;
}

function togglePlanSubOptions() {
  const planCb = document.querySelector('.run-step-cb[data-step="plan"]');
  const sub = document.querySelector('.run-step-sub');
  if (!sub) return;
  const enabled = planCb?.checked ?? true;
  sub.style.opacity = enabled ? '1' : '0.35';
  sub.querySelectorAll('input, button').forEach(el => el.disabled = !enabled);
}

function getRunButtonText() {
  if (state.selectionMode && state.selectedFiles.length > 0) {
    return `${icon('play', 16)} 运行选中步骤 (${state.selectedFiles.length})`;
  }
  if (state.selectionMode && state.selectedFiles.length === 0) {
    return `${icon('play', 16)} 请先勾选视频`;
  }
  return `${icon('play', 16)} 运行选中步骤`;
}

function updateRunStartButtonState() {
  const btn = $('btn-run-start');
  if (!btn || _runActive) return;
  const noSelection = state.selectionMode && state.selectedFiles.length === 0;
  btn.disabled = noSelection;
  btn.innerHTML = getRunButtonText();
  btn.title = noSelection ? '选择模式下请先勾选至少一个视频' : '';
}

function collectRunOptions() {
  const steps = [...document.querySelectorAll('.run-step-cb:checked')].map(cb => cb.dataset.step);
  const options = {
    day_label: ($('run-day')?.value.trim() || state.currentDay || 'day1'),
    steps,
    use_transcripts: $('run-use-transcripts')?.checked ?? true,
    overwrite: !!$('run-overwrite')?.checked,
  };
  if (state.selectionMode && state.selectedFiles.length > 0) {
    options.files = state.selectedFiles.slice();
  }
  const contextOverride = $('run-context-override')?.value?.trim();
  if (contextOverride) {
    options.context_override = contextOverride;
  }
  return options;
}

function renderRunPreviewHtml(preview) {
  if (!preview) {
    return '<p class="muted">选择步骤后显示预览</p>';
  }
  const input = preview.input || {};
  const totals = preview.totals || {};
  const steps = Array.isArray(preview.steps) ? preview.steps : [];
  const stepRows = steps.map(step => {
    const warnings = (step.warnings || []).map(w => `<div class="warn">${escapeHtml(w)}</div>`).join('');
    return `
      <div class="run-preview-step">
        <span class="run-preview-name">${escapeHtml(step.label || step.name || '')}</span>
        <span>总数 ${Number(step.total || 0)}</span>
        <span>待执行 ${Number(step.will_run || 0)}</span>
        <span>跳过 ${Number(step.will_skip || 0)}</span>
        ${warnings}
      </div>
    `;
  }).join('');
  const warningLine = Number(totals.warnings || 0) > 0
    ? `<p class="warn">警告 ${Number(totals.warnings || 0)} 项，请确认后再运行。</p>`
    : '';
  return `
    <section class="run-preview-box">
      <h4 style="margin:0 0 6px">运行预览</h4>
      <p class="muted">输入：${escapeHtml(input.path || '')}（${Number(input.count || 0)} 个）</p>
      <div class="run-preview-totals">
        <span>步骤 ${Number(totals.selected_steps || 0)}</span>
        <span>待执行 ${Number(totals.will_run || 0)}</span>
        <span>跳过 ${Number(totals.will_skip || 0)}</span>
      </div>
      ${warningLine}
      <div class="run-preview-steps">${stepRows}</div>
    </section>
  `;
}

async function refreshRunPreview({ silent = false } = {}) {
  const container = $('run-preview');
  if (!container) return null;
  const options = collectRunOptions();
  if (!options.steps.length) {
    container.innerHTML = renderRunPreviewHtml(null);
    return null;
  }
  const ac = beginLatest('run-preview');
  if (!silent) {
    container.innerHTML = '<p class="muted">正在生成运行预览...</p>';
  }
  try {
    const response = await api('POST', '/api/run/preview', options, { signal: ac.signal });
    if (!isLatest('run-preview', ac)) return null;
    if (response.preview) {
      container.innerHTML = renderRunPreviewHtml(response.preview);
    } else {
      container.innerHTML = renderRunPreviewHtml(null);
    }
  } catch (e) {
    if (isAbortError(e) || !isLatest('run-preview', ac)) return null;
    container.innerHTML = renderRunPreviewHtml(null);
  } finally {
    endLatest('run-preview', ac);
  }
  return null;
}

function updateRunFilesBadge() {
  const badge = $('run-files-badge');
  if (badge) {
    if (state.selectionMode && state.selectedFiles.length > 0) {
      badge.textContent = `(${state.selectedFiles.length} 个视频)`;
      badge.style.display = 'inline';
    } else if (state.selectionMode) {
      badge.textContent = '(未勾选视频)';
      badge.style.display = 'inline';
    } else {
      badge.style.display = 'none';
    }
  }
  // Overwrite is always available on the Run tab (not only multi-select)
  const overwrap = $('option-overwrite-wrap');
  if (overwrap) overwrap.style.display = 'flex';
  updateRunStartButtonState();
  if ($('run-preview')) refreshRunPreview({ silent: true });
}

async function startRun() {
  const btn = $('btn-run-start');
  if (btn.disabled) return;
  if (state.selectionMode && state.selectedFiles.length === 0) {
    setStatus('选择模式下请先勾选至少一个视频', 'warn');
    addToast('请先勾选视频', 'warning');
    return;
  }
  btn.disabled = true;
  btn.textContent = '启动中...';
  const options = collectRunOptions();
  if (!options.steps.length) {
    updateRunStartButtonState();
    setStatus('请至少选择一个步骤', 'warn');
    return;
  }
  if (state.deps && state.deps.ok === false && mediaStepsNeedFfmpeg(options.steps)) {
    const msg = state.deps.detail || '需要 ffmpeg/ffprobe 才能运行所选步骤';
    setStatus(msg, 'warn');
    addToast(msg, 'warning', 6000);
    updateRunStartButtonState();
    return;
  }
  _lastRunDay = options.day_label;
  _lastRunSteps = options.steps.slice();
  _expectDoneNavigation = true;
  _stopRunSSE();
  try {
    const r = await api('POST', '/api/run/start', options);
    if (r.ok) {
      _runActive = true;
      const msg = r.message || '流水线已启动';
      setStatus(msg, 'ok');
      addToast(msg, 'success');
      $('run-progress').innerHTML = '<p class="muted">流水线已启动，等待进度...</p>';
      _startRunSSE();
    } else {
      throw new Error(r.error || '启动失败');
    }
  } catch (e) {
    $('run-progress').innerHTML = `<p class="err">${escapeHtml(e.message)}</p>`;
    const msg = '启动失败: ' + e.message;
    setStatus(msg, 'err');
    addToast(msg, 'error', 6000);
    _runActive = false;
    updateRunStartButtonState();
  }
}

async function cancelRun() {
  const btn = $('btn-run-cancel');
  if (btn) { btn.disabled = true; btn.innerHTML = '⏹ 正在取消...'; }
  try {
    const r = await api('POST', '/api/run/cancel', {});
    const msg = r.message || '取消请求已发送';
    setStatus(msg, 'warn');
    addToast(msg, 'warning');
  } catch (e) {
    const msg = '取消失败: ' + e.message;
    setStatus(msg, 'err');
    addToast(msg, 'error', 6000);
    if (btn) { btn.disabled = false; btn.innerHTML = '取消'; }
  }
}

function _startRunSSE() {
  _stopRunSSE();
  let url = '/api/run/stream';
  let sep = '?';
  const addQuery = (key, value) => {
    if (!value) return;
    url += sep + key + '=' + encodeURIComponent(value);
    sep = '&';
  };
  if (state.currentProjectName) {
    addQuery('project', state.currentProjectName);
  }
  if (state.currentProjectDir) {
    addQuery('project_dir', state.currentProjectDir);
  }
  addQuery('token', sessionStorage.getItem('api_token'));
  _runEventSource = new EventSource(url);
  _runEventSource.onmessage = (event) => {
    try {
      const s = JSON.parse(event.data);
      _handleRunStatus(s);
    } catch { /* ignore parse errors */ }
  };
  _runEventSource.onerror = () => {
    // EventSource auto-reconnects on connection loss
  };
}

function _stopRunSSE() {
  if (_runEventSource) {
    _runEventSource.close();
    _runEventSource = null;
  }
}

/** Re-fetch the video list so artifacts from a finished/cancelled/failed run show up. */
async function refreshVideosAfterRun() {
  try {
    await import('./sidebar.js').then(mod => mod.loadVideos());
  } catch { /* 刷新失败不阻断完成后的导航/提示 */ }
}

/** Stale-progress warning box; Whisper model hint only for the transcribe phase. */
function staleWarningHtml(phase) {
  const whisperHint = phase === 'transcribe'
    ? `<span style="color:var(--text-secondary)">可前往 <a href="#" id="link-stale-settings" style="text-decoration:underline;color:var(--accent)">设置 → Whisper 模型管理</a> 检查模型状态</span>`
    : '';
  return `
    <div id="stale-warn" style="display:none;margin-top:8px;padding:8px;background:var(--warning-bg,#2a2520);border:1px solid var(--warning-border,#b8860b);border-radius:6px;font-size:var(--text-sm)">
      ⏳ 进度长时间未更新，可能仍在运行或网络连接异常<br>
      ${whisperHint}
    </div>
  `;
}

async function _handleRunStatus(s) {
  const prog = $('run-progress');
  const btn = $('btn-run-start');
  // Pane may be unmounted (user switched to Plan/video) — still process terminal
  // status so navigation / plan reload / toast still run; only skip DOM paint.
  const hasRunDom = !!prog;
  if (s.rerun) return;
    if (s.status === 'idle' || s.status === 'unknown') {
      _lastProgressSnapshot = null;
      _runActive = false;
      if (btn) {
        btn.disabled = false;
        updateRunStartButtonState();
      }
      const cancelBtn = $('btn-run-cancel');
      if (cancelBtn) cancelBtn.style.display = 'none';
      if (hasRunDom && !s.running) {
        prog.innerHTML = '<p class="muted">尚未运行</p>';
        renderProcessingState($('run-state-container'));
      }
      return;
    }
    if (s.status === 'running') {
      const stale = !s.running;
      if (stale) {
        _runActive = false;
        updateRunStartButtonState();
        const cancelBtn = $('btn-run-cancel');
        if (cancelBtn) cancelBtn.style.display = 'none';
        if (hasRunDom) {
          const logsHtml = s.logs?.length ? `<div class="run-logs">${s.logs.map(l => `<div class="run-log-line">${escapeHtml(l)}</div>`).join('')}</div>` : '';
          prog.innerHTML = `
            <p class="warn">⚠ 上次运行时意外中断，以下为残留进度（已失效）</p>
            <p><strong>阶段:</strong> ${escapeHtml(s.phase || '')}</p>
            <p><strong>进度:</strong> ${s.current}/${s.total}</p>
            <p><strong>状态:</strong> ${escapeHtml(s.message || '')}</p>
            ${logsHtml}
          `;
          renderProcessingState($('run-state-container'));
        }
      } else {
        _seenNonTerminal = true;
        if (btn) { btn.disabled = true; btn.textContent = '运行中...'; }
        const cancelBtn = $('btn-run-cancel');
        if (cancelBtn) { cancelBtn.style.display = ''; cancelBtn.disabled = false; }
        if (hasRunDom) {
          const pct = s.total > 0 ? Math.round(s.current / s.total * 100) : 0;
          const eta = s.eta_sec ? `，预计剩余 ${Math.round(s.eta_sec)} 秒` : '';
          const logsHtml = s.logs?.length ? `<div class="run-logs">${s.logs.map(l => `<div class="run-log-line">${escapeHtml(l)}</div>`).join('')}</div>` : '';
          prog.innerHTML = `
            <p><strong>阶段:</strong> ${escapeHtml(s.phase || '')}</p>
            <p><strong>进度:</strong> ${s.current}/${s.total} (${pct}%)${eta}</p>
            <p><strong>状态:</strong> ${escapeHtml(s.message || '')}</p>
            <div style="background:#333;border-radius:3px;height:8px;margin:8px 0">
              <div style="background:var(--accent);border-radius:3px;height:100%;width:${pct}%"></div>
            </div>
            ${staleWarningHtml(s.phase)}
            ${logsHtml}
          `;
          // 超时停滞检测：如果 current/total/message 无变化超过 60 秒，显示提示
          const snapKey = s.current + '/' + s.total + '/' + s.message;
          const now = Date.now();
          if (!_lastProgressSnapshot || _lastProgressSnapshot.key !== snapKey) {
            _lastProgressSnapshot = { key: snapKey, timestamp: now };
          } else if (now - _lastProgressSnapshot.timestamp > 60000) {
            var staleEl = $('stale-warn');
            if (staleEl) staleEl.style.display = '';
            var staleSettingsLink = $('link-stale-settings');
            if (staleSettingsLink) {
              staleSettingsLink.onclick = function(e) { e.preventDefault(); import('./sidebar.js').then(function(s) { s.selectConfig(); }); };
            }
          }
        }
      }
    } else if (s.status === 'done') {
      _lastProgressSnapshot = null;
      _runActive = false;
      _stopRunPoll();
      updateRunStartButtonState();
      const cancelBtn = $('btn-run-cancel');
      if (cancelBtn) cancelBtn.style.display = 'none';
      if (hasRunDom) {
        const logsHtml = s.logs?.length ? `<div class="run-logs">${s.logs.map(l => `<div class="run-log-line">${escapeHtml(l)}</div>`).join('')}</div>` : '';
        prog.innerHTML = `<p class="ok">✓ 流水线完成</p><p>${escapeHtml(s.message || '')}</p>${logsHtml}`;
      }
      setStatus('流水线完成', 'ok');
      addToast(s.message || '流水线完成', 'success');
      if (hasRunDom) renderProcessingState($('run-state-container'));
      // 检查是否有转录失败（如缺少模型），弹出下载引导
      if (hasRunDom) {
        (async () => {
          try {
            const ps = await api('GET', '/api/processing-state');
            const hasTranscribeErr = Object.values(ps.files || {}).some(function(f) { return f.transcribe === 'error'; });
            if (hasTranscribeErr) {
              const warn = document.createElement('div');
              warn.id = 'run-transcribe-warn';
              warn.style.cssText = 'margin-top:12px;padding:12px;background:var(--warning-bg,#2a2520);border:1px solid var(--warning-border,#b8860b);border-radius:6px';
              warn.innerHTML = `
                <p style="margin:0 0 8px;font-weight:600">❗ 部分视频转录失败</p>
                <p style="margin:0 0 8px;font-size:var(--text-sm);color:var(--text-secondary)">Whisper 模型未下载，请前往 <a href="#" id="link-go-settings" style="text-decoration:underline;color:var(--accent)">设置 → Whisper 模型管理</a> 手动下载模型（约 1-2 GB），再重跑「Whisper 转录」。</p>
              `;
              prog.appendChild(warn);
              var settingsLink = $('link-go-settings');
              if (settingsLink) {
                settingsLink.onclick = function(e) { e.preventDefault(); import('./sidebar.js').then(function(s) { s.selectConfig(); }); };
              }
            }
          } catch { /* 静默 */ }
        })();
      }
      state.currentDay = _lastRunDay;
      state.plan = null;
      await import('./sidebar.js').then(mod => mod.loadPlans());
      updateSidebarDay();
      import('./sidebar.js').then(mod => mod.renderVideoList());
      import('./sidebar.js').then(mod => mod.saveProject());
      try { state.plan = await api('GET', `/api/plan?day=${_lastRunDay}`); } catch {}
      await refreshVideosAfterRun();
      const completedSteps = Array.isArray(s.steps) ? s.steps : _lastRunSteps;
      if (state.currentEntity === 'run' && (_expectDoneNavigation || _seenNonTerminal)) {
        await _showRunCompletionTarget(completedSteps);
      } else if (state.currentEntity === 'plan') {
        import('./sidebar.js').then(mod => mod.selectPlan());
      }
      _expectDoneNavigation = false;
    } else if (s.status === 'cancelled') {
      _lastProgressSnapshot = null;
      _runActive = false;
      _stopRunPoll();
      updateRunStartButtonState();
      const cancelBtn = $('btn-run-cancel');
      if (cancelBtn) cancelBtn.style.display = 'none';
      if (hasRunDom) {
        const logsHtml = s.logs?.length ? `<div class="run-logs">${s.logs.map(l => `<div class="run-log-line">${escapeHtml(l)}</div>`).join('')}</div>` : '';
        prog.innerHTML = `<p class="warn">⏹ 流水线已取消</p><p>${escapeHtml(s.message || '')}</p>${logsHtml}`;
      }
      setStatus('流水线已取消', 'warn');
      addToast(s.message || '流水线已取消', 'warning');
      if (hasRunDom) renderProcessingState($('run-state-container'));
      await refreshVideosAfterRun();
    } else if (s.status === 'error') {
      _lastProgressSnapshot = null;
      _runActive = false;
      _stopRunPoll();
      updateRunStartButtonState();
      const cancelBtn = $('btn-run-cancel');
      if (cancelBtn) cancelBtn.style.display = 'none';
      if (hasRunDom) {
        const logsHtml = s.logs?.length ? `<div class="run-logs">${s.logs.map(l => `<div class="run-log-line">${escapeHtml(l)}</div>`).join('')}</div>` : '';
        prog.innerHTML = `<p class="err">✗ 流水线出错</p><p>${escapeHtml(s.message || '')}</p>${logsHtml}`;
      }
      setStatus('流水线出错', 'err');
      addToast(s.message || '流水线出错', 'error', 6000);
      if (hasRunDom) renderProcessingState($('run-state-container'));
      await refreshVideosAfterRun();
    }
}

function _stopRunPoll() {
  _stopRunSSE();
}

function _completionTargetForSteps(steps) {
  const stepSet = new Set(Array.isArray(steps) ? steps : []);
  if (stepSet.has('plan')) return { entity: 'plan' };
  if (stepSet.has('voiceover')) return { entity: 'video', tab: 'voiceover' };
  if (stepSet.has('transcribe')) return { entity: 'video', tab: 'transcript' };
  if (stepSet.has('analyze')) return { entity: 'video', tab: 'texts' };
  if (stepSet.has('compress') || stepSet.has('label')) return { entity: 'video', tab: state.currentTab || 'texts' };
  return null;
}

async function _showRunCompletionTarget(steps) {
  const target = _completionTargetForSteps(steps);
  if (!target) return;
  const sidebar = await import('./sidebar.js');
  if (target.entity === 'plan') {
    await sidebar.selectPlan(_lastRunDay);
    return;
  }
  state.currentTab = target.tab;
  if (state.source !== 'compressed') {
    await sidebar.setSource('compressed');
    return;
  }
  const preferred = state.currentVideo && state.videos.some(v => v.file === state.currentVideo)
    ? state.currentVideo
    : state.videos[0]?.file;
  if (preferred) {
    await sidebar.selectVideo(preferred);
  }
}

const _STEP_LABELS_SHORT = {
  compress: '压缩',
  analyze: '分析',
  voiceover: '口播',
  transcribe: '转录',
  plan: '规划',
  label: '标号',
};
const _STATUS_ICON = { done: '✅', skipped: '⏭️', error: '✗', cancelled: '⏹', running: '…' };
const _SKIP_REASON_HINTS = {
  compress: '已找到可复用的压缩文件或分段输出。勾选"覆盖现有输出"后会重新压缩。',
  analyze: '通常是分析 JSON 已存在；也可能是视频超过 analyze.max_analyze_duration_min 时长限制。',
  voiceover: '口播 JSON 已存在。需要重写文案时勾选"覆盖现有输出"。',
  transcribe: '通常是转录 JSON 已存在；也可能是找不到原始视频或音频提取失败。',
  plan: '剪辑规划文件已存在。需要重建规划时勾选"覆盖现有输出"。',
  label: '可能是标号视频已存在，或找不到对应的压缩视频。',
};

async function renderProcessingState(container) {
  try {
    const st = await api('GET', '/api/processing-state');
    const files = st.files;
    const stepKeys = ['compress', 'analyze', 'voiceover', 'transcribe', 'plan', 'label'];
    const entries = Object.entries(files).sort((a, b) => a[0].localeCompare(b[0]));
    if (!entries.length) { if (container) container.innerHTML = ''; return; }
    let html = '<h4 style="margin:12px 0 4px">处理状态</h4><div class="state-table"><div class="state-row state-header"><span class="state-file">文件</span>';
    for (const k of stepKeys) html += `<span class="state-cell">${_STEP_LABELS_SHORT[k]}</span>`;
    html += '</div>';
    for (const [file, steps] of entries) {
      html += `<div class="state-row"><span class="state-file">${escapeHtml(file)}</span>`;
      for (const k of stepKeys) {
        const v = steps[k];
        html += `<span class="state-cell">${v ? _STATUS_ICON[v] || v : ''}</span>`;
      }
      html += '</div>';
    }
    html += '</div>';
    html += renderSkippedDiagnosticsHtml(buildSkippedDiagnostics(st));
    if (container) container.innerHTML = html;
  } catch { /* ignore */ }
}

function buildSkippedDiagnostics(processingState) {
  const files = processingState?.files || {};
  const stateSteps = Array.isArray(processingState?.steps) ? processingState.steps : Object.keys(_STEP_LABELS_SHORT);
  const stepKeys = stateSteps.filter(step => step in _STEP_LABELS_SHORT);
  const diagnostics = [];
  for (const [file, steps] of Object.entries(files).sort((a, b) => a[0].localeCompare(b[0]))) {
    if (!steps || typeof steps !== 'object') continue;
    for (const step of stepKeys) {
      if (steps[step] !== 'skipped') continue;
      diagnostics.push({
        file,
        step,
        label: _STEP_LABELS_SHORT[step] || step,
        reason: _SKIP_REASON_HINTS[step] || '该步骤被记录为 skipped；请检查运行日志和对应输出文件。',
      });
    }
  }
  return diagnostics;
}
function renderSkippedDiagnosticsHtml(diagnostics) {
  const rows = (diagnostics || []).map(item => `
    <div class="skip-row">
      <span class="skip-file">${escapeHtml(item.file)}</span>
      <span class="skip-step">${escapeHtml(item.label || item.step)}</span>
      <span class="skip-reason">${escapeHtml(item.reason)}</span>
    </div>
  `).join('');
  return `
    <details class="skip-panel" ${rows ? 'open' : ''}>
      <summary>为什么被跳过</summary>
      <p class="muted">基于 .processing.json 的 skipped 状态推断；精确原因以运行日志和实际输出文件为准。</p>
      ${rows ? `<div class="skip-table">${rows}</div>` : '<p class="muted">当前没有 skipped 记录。</p>'}
    </details>
  `;
}
export {
  renderRun,
  startRun,
  _stopRunPoll,
  updateRunFilesBadge,
  updateRunStartButtonState,
  getRunButtonText,
  collectRunOptions,
  _completionTargetForSteps,
  _shouldResetRunNavigationOnRender,
  renderRunPreviewHtml,
  buildSkippedDiagnostics,
  renderSkippedDiagnosticsHtml,
  refreshVideosAfterRun,
  staleWarningHtml,
};
