import { state, setArtifactVersion } from './state.js';
import {
  $,
  escapeHtml,
  setStatus,
  updateSidebarDay,
} from './utils.js';
import { api, apiWithMeta, icon } from './api.js';
import { beginLatest, endLatest, isLatest, isAbortError } from './latest.js';
import { addToast } from './toast.js';
import { registerNotification } from './notification-center.js';
import { subscribeTaskEvents, fetchTask } from './task-center.js';
import { transcriptionFailureNotice } from './transcription-status.js';
import { onLocaleChange, t } from './i18n.js';
import { confirmDialog } from './confirm.js';
import {
  formatRunStartError,
  needsAnalysisRerunConfirmation,
  needsArtifactOverwriteConfirmation,
  needsLegacyAudioRemovalConfirmation,
  needsPlanOverwriteConfirmation,
  renderRunPreviewHtml,
} from './runner-preview.js';
import { buildSkippedDiagnostics, renderProcessingState, renderSkippedDiagnosticsHtml, STEP_KEYS, stepShortLabel } from './runner-diagnostics.js';

let _lastRunDay = 'day1';
let _runActive = false;
let _lastProgressSnapshot = null;
let _lastRunSteps = [];
let _expectDoneNavigation = false;
let _seenNonTerminal = false;
let _managedTaskId = null;
let _taskEventsUnsubscribe = null;

const STEPS_KEY = 'vlog_ui_run_steps';

const RUN_STEPS = [
  { key: 'compress' },
  { key: 'transcribe' },
  { key: 'analyze' },
  { key: 'consistency' },
  { key: 'voiceover' },
  { key: 'plan' },
  { key: 'plan_voiceover' },
  { key: 'label' },
];

const FFMPEG_RUN_STEPS = new Set(['compress', 'label', 'transcribe']);
const MEDIA_STEP_DEPENDENCIES = {
  compress: ['ffmpeg', 'ffprobe'],
  label: ['ffmpeg'],
  transcribe: ['ffmpeg', 'ffprobe'],
};

/** True if any selected step hard-requires ffmpeg/ffprobe. */
export function mediaStepsNeedFfmpeg(steps) {
  return (Array.isArray(steps) ? steps : []).some((s) => FFMPEG_RUN_STEPS.has(s));
}

/** Return only missing binaries that affect the selected media steps. */
export function missingMediaDepsForSteps(steps, deps) {
  const missing = Array.isArray(deps?.missing) ? deps.missing : [];
  const required = new Set((Array.isArray(steps) ? steps : [])
    .flatMap(step => MEDIA_STEP_DEPENDENCIES[step] || []));
  return missing.filter(binary => required.has(binary));
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
    const isConsistency = s.key === 'consistency';
    const isPlan = s.key === 'plan';
    return `
      <div class="run-step-wrap">
        <label class="run-step ${isPlan ? 'run-step-plan' : ''}">
          <input type="checkbox" class="run-step-cb" data-step="${s.key}" ${checked ? 'checked' : ''}>
          <span class="run-step-label">${t(`run.step.${s.key}.label`)}</span>
          <span class="run-step-hint">${t(`run.step.${s.key}.hint`)}</span>
        </label>
        ${isConsistency ? `
        <div class="run-step-sub run-step-day-sub">
          <label class="run-option">
            <span class="run-option-label">${t('run.day_label')}</span>
            <input id="run-day" class="run-option-input" value="${escapeHtml(state.currentDay)}">
          </label>
          <label class="run-option run-option-check">
            <input type="checkbox" id="run-all-days">
            <span>${t('run.all_days')}</span>
          </label>
        </div>
        ` : ''}
        ${isPlan ? `
        <div class="run-step-sub run-step-plan-sub">
          <label class="run-option run-option-check">
            <input type="checkbox" id="run-use-transcripts" ${savedUseTrans ? 'checked' : ''}>
            <span>${t('run.use_transcripts')}</span>
          </label>
        </div>
        ` : ''}
      </div>
    `;
  }).join('');

  pane.innerHTML = `
    <h3>${t('run.title')}</h3>
    <p class="hint">${t('run.subtitle')}</p>
    <p class="hint">${t('run.current_project', { path: escapeHtml(state.currentProjectDir || state.config?.project_dir || t('run.unset')) })}</p>
    <div class="run-step-list">${stepChecks}</div>
    <details class="run-prompt-section" style="margin:12px 0">
      <summary style="cursor:pointer;font-size:var(--text-sm);color:var(--text-secondary);user-select:none">${t('run.advanced_prompt')}</summary>
      <div style="margin-top:8px">
        <textarea id="run-context-override" class="run-prompt-input" placeholder="${t('run.context_placeholder')}" rows="4" style="width:100%;box-sizing:border-box;padding:8px;border:1px solid var(--border);border-radius:4px;background:var(--bg-input,#1e1e1e);color:var(--text-primary);font-size:var(--text-sm);resize:vertical;font-family:inherit"></textarea>
      </div>
    </details>
    <div style="display:flex;gap:8px;align-items:center;margin-top:12px;flex-wrap:wrap">
      <button id="btn-run-start" class="btn-primary">${getRunButtonText()}</button>
      <span id="run-files-badge" class="run-files-badge" style="display:none"></span>
      <button id="btn-run-cancel" class="btn-secondary" style="display:none">${t('common.cancel')}</button>
      <label class="run-option-check" id="option-overwrite-wrap">
        <input type="checkbox" id="run-overwrite">
        <span>${t('run.overwrite')}</span>
      </label>
    </div>
    <div id="run-preview" style="margin-top:12px"></div>
    <div id="run-progress" style="margin-top:12px"></div>
    <div id="run-task-link" class="run-task-link" hidden></div>
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
  const allDaysCb = $('run-all-days');
  if (allDaysCb) {
    allDaysCb.addEventListener('change', () => {
      toggleDayScopeInputs();
      refreshRunPreview({ silent: true });
    });
  }

  togglePlanSubOptions();
  updateRunFilesBadge();
  updateRunStartButtonState();
  refreshRunPreview({ silent: true });

  const runBtn = $('btn-run-start');
  runBtn.onclick = startRun;
  if (_runActive) { runBtn.disabled = true; runBtn.textContent = t('run.btn.running'); }
  const cancelBtn = $('btn-run-cancel');
  if (cancelBtn) cancelBtn.onclick = cancelRun;
  _subscribeManagedRunEvents();
  _syncManagedRunTask();
}

function _shouldResetRunNavigationOnRender(runActive) {
  return !runActive;
}

function togglePlanSubOptions() {
  const consistencyCb = document.querySelector('.run-step-cb[data-step="consistency"]');
  const planCb = document.querySelector('.run-step-cb[data-step="plan"]');
  const planVoiceoverCb = document.querySelector('.run-step-cb[data-step="plan_voiceover"]');
  const dayEnabled = consistencyCb?.checked
    || planCb?.checked
    || planVoiceoverCb?.checked
    || (!consistencyCb && !planCb && !planVoiceoverCb);
  const daySub = document.querySelector('.run-step-day-sub');
  if (daySub) {
    daySub.style.opacity = dayEnabled ? '1' : '0.35';
    daySub.querySelectorAll('input, button').forEach(el => el.disabled = !dayEnabled);
  }
  toggleDayScopeInputs(dayEnabled);
  const planSub = document.querySelector('.run-step-plan-sub');
  if (!planSub) return;
  const transcriptsEnabled = planCb?.checked || planVoiceoverCb?.checked || (!planCb && !planVoiceoverCb);
  planSub.style.opacity = transcriptsEnabled ? '1' : '0.35';
  planSub.querySelectorAll('input, button').forEach(el => el.disabled = !transcriptsEnabled);
}

function toggleDayScopeInputs(dayEnabledOverride) {
  const allDaysCb = $('run-all-days');
  const dayInput = $('run-day');
  if (!allDaysCb || !dayInput) return;
  const consistencyCb = document.querySelector('.run-step-cb[data-step="consistency"]');
  const planCb = document.querySelector('.run-step-cb[data-step="plan"]');
  const planVoiceoverCb = document.querySelector('.run-step-cb[data-step="plan_voiceover"]');
  const dayEnabled = dayEnabledOverride ?? (
    consistencyCb?.checked
    || planCb?.checked
    || planVoiceoverCb?.checked
    || (!consistencyCb && !planCb && !planVoiceoverCb)
  );
  if (state.selectionMode) allDaysCb.checked = false;
  allDaysCb.disabled = !dayEnabled || state.selectionMode;
  dayInput.disabled = !dayEnabled || allDaysCb.checked;
}

function getRunButtonText() {
  if (state.selectionMode && state.selectedFiles.length > 0) {
    return `${icon('play', 16)} ${t('run.btn.start_count', { count: state.selectedFiles.length })}`;
  }
  if (state.selectionMode && state.selectedFiles.length === 0) {
    return `${icon('play', 16)} ${t('run.btn.pick_first')}`;
  }
  return `${icon('play', 16)} ${t('run.btn.start')}`;
}

function updateRunStartButtonState() {
  const btn = $('btn-run-start');
  if (!btn || _runActive) return;
  const noSelection = state.selectionMode && state.selectedFiles.length === 0;
  toggleDayScopeInputs();
  btn.disabled = noSelection;
  btn.innerHTML = getRunButtonText();
  btn.title = noSelection ? t('run.pick_first_title') : '';
}

function collectRunOptions() {
  const steps = [...document.querySelectorAll('.run-step-cb:checked')].map(cb => cb.dataset.step);
  const options = {
    day_label: ($('run-day')?.value.trim() || state.currentDay || 'day1'),
    steps,
    use_transcripts: $('run-use-transcripts')?.checked ?? true,
    overwrite: !!$('run-overwrite')?.checked,
  };
  if ($('run-all-days')?.checked) {
    options.all_days = true;
  }
  if (state.selectionMode && state.selectedFiles.length > 0) {
    options.files = state.selectedFiles.slice();
  }
  const contextOverride = $('run-context-override')?.value?.trim();
  if (contextOverride) {
    options.context_override = contextOverride;
  }
  return options;
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
    container.innerHTML = `<p class="muted">${t('run.preview.generating')}</p>`;
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
      badge.textContent = t('run.badge.count', { count: state.selectedFiles.length });
      badge.style.display = 'inline';
    } else if (state.selectionMode) {
      badge.textContent = t('run.badge.none');
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
    setStatus(t('run.pick_first_title'), 'warn');
    addToast(t('run.btn.pick_first'), 'warning', undefined, { persist: false });
    return;
  }
  btn.disabled = true;
  btn.textContent = t('run.starting');
  const options = collectRunOptions();
  if (!options.steps.length) {
    updateRunStartButtonState();
    setStatus(t('run.need_steps'), 'warn');
    return;
  }
  const missingMediaDeps = missingMediaDepsForSteps(options.steps, state.deps);
  if (state.deps && state.deps.ok === false && missingMediaDeps.length > 0) {
    const msg = state.deps.detail || t('run.deps_required', { items: missingMediaDeps.join(t('common.list_separator')) });
    setStatus(msg, 'warn');
    addToast(msg, 'warning', 6000, { persist: false });
    updateRunStartButtonState();
    return;
  }
  try {
    if (options.steps.some(step => ['plan', 'voiceover', 'plan_voiceover', 'analyze', 'compress'].includes(step))) {
      const previewResponse = await api('POST', '/api/run/preview', options);
      const preview = previewResponse?.preview || null;
      if (needsPlanOverwriteConfirmation(preview, options)) {
        const day = preview.plan_overwrite.day_label || options.day_label;
        if (!(await confirmDialog(t('run.confirm_plan_overwrite', { day }), { danger: true }))) {
          updateRunStartButtonState();
          return;
        }
        options.confirm_plan_overwrite = true;
      }
      if (needsArtifactOverwriteConfirmation(preview, options)) {
        const items = (preview.artifact_overwrite.items || []).join(t('common.list_separator'));
        if (!(await confirmDialog(t('run.confirm_artifact_overwrite', { items }), { danger: true }))) {
          updateRunStartButtonState();
          return;
        }
        options.confirm_artifact_overwrite = true;
      }
      if (needsLegacyAudioRemovalConfirmation(preview, options)) {
        const risk = preview.legacy_audio_removal;
        if (!(await confirmDialog(t('run.confirm_legacy_audio_removal', {
          analyze_count: Number(risk.analyze_count || 0),
          voiceover_count: Number(risk.voiceover_count || 0),
          compress_count: Number(risk.compress_count || 0),
        }), { danger: true }))) {
          updateRunStartButtonState();
          return;
        }
        options.confirm_legacy_audio_removal = true;
      }
      if (needsAnalysisRerunConfirmation(preview, options)) {
        if (!(await confirmDialog(t('run.confirm_analysis_rerun', {
          count: Number(preview.analysis_rerun.count || 0),
        }), { danger: true }))) {
          updateRunStartButtonState();
          return;
        }
        options.confirm_analysis_rerun = true;
      }
    }
    _lastRunDay = options.day_label;
    _lastRunSteps = options.steps.slice();
    _expectDoneNavigation = true;
    const r = await api('POST', '/api/run/start', options);
    if (r.ok) {
      _managedTaskId = r.task_id || r.task?.id || null;
      renderManagedTaskLink();
      _runActive = true;
      const msg = r.message || t('run.started');
      setStatus(msg, 'ok', { persist: false });
      addToast(msg, 'success', undefined, { persist: false });
      $('run-progress').innerHTML = `<p class="muted">${t('run.started_waiting')}</p>`;
      if (_managedTaskId) {
        _subscribeManagedRunEvents();
        _syncManagedTaskSnapshot(_managedTaskId);
      }
    } else {
      throw new Error(r.error || t('run.start_failed'));
    }
  } catch (e) {
    const display = formatRunStartError(e);
    $('run-progress').innerHTML = `<p class="err">${escapeHtml(display)}</p>`;
    const msg = t('run.start_failed_prefix', { detail: display });
    setStatus(msg, 'err');
    addToast(msg, 'error', 6000, { title: t('run.start_failed_title') });
    _runActive = false;
    updateRunStartButtonState();
  }
}

async function cancelRun() {
  const btn = $('btn-run-cancel');
  if (btn) { btn.disabled = true; btn.innerHTML = t('run.cancelling'); }
  try {
    const r = await api('POST', `/api/tasks/${encodeURIComponent(_managedTaskId)}/cancel`, {});
    const msg = r.message || t('run.cancel_requested');
    setStatus(msg, 'warn');
    addToast(msg, 'warning', undefined, { persist: false });
  } catch (e) {
    const msg = t('run.cancel_failed', { detail: e.message });
    setStatus(msg, 'err');
    addToast(msg, 'error', 6000, { title: t('run.cancel_failed_title') });
    if (btn) { btn.disabled = false; btn.innerHTML = t('common.cancel'); }
  }
}

function renderManagedTaskLink() {
  const el = $('run-task-link');
  if (!el || !_managedTaskId) return;
  el.hidden = false;
  el.innerHTML = `<button type="button" class="btn-secondary">${t('run.view_in_tasks')}</button>`;
  el.querySelector('button').onclick = () => import('./sidebar.js').then(mod => mod.selectTasks());
}

function _taskToRunStatus(task, event = null) {
  const status = task?.status;
  const mapped = status === 'succeeded' ? 'done'
    : status === 'failed' || status === 'interrupted' ? 'error'
      : status === 'cancelled' ? 'cancelled'
        : status === 'queued' || status === 'cancelling' || status === 'running' ? 'running'
          : 'unknown';
  const current = Number(task?.current) || 0;
  const total = Number(task?.total) || 0;
  return {
    status: mapped,
    running: mapped === 'running',
    current,
    total,
    progress_pct: task?.progress_pct,
    phase: task?.phase || '',
    message: event?.event?.message || task?.message || task?.error_message || '',
    task_id: task?.id,
    steps: task?.input_summary?.steps || _lastRunSteps,
  };
}

function _sameRunProject(task) {
  const project = state.currentProjectDir || state.config?.project_dir;
  return !project || !task?.project_id || String(task.project_id) === String(project);
}

function _subscribeManagedRunEvents() {
  if (_taskEventsUnsubscribe) return;
  _taskEventsUnsubscribe = subscribeTaskEvents(payload => {
    const task = payload?.task;
    if (!task || task.kind !== 'pipeline' || !_sameRunProject(task)) return;
    if (_managedTaskId && task.id !== _managedTaskId) return;
    _managedTaskId = task.id;
    renderManagedTaskLink();
    _handleRunStatus(_taskToRunStatus(task, payload));
  });
}

async function _syncManagedTaskSnapshot(taskId) {
  try {
    const task = await fetchTask(taskId);
    if (!task || task.id !== _managedTaskId || task.kind !== 'pipeline') return;
    _handleRunStatus(_taskToRunStatus(task));
  } catch { /* stream remains the primary source */ }
}

async function _syncManagedRunTask() {
  try {
    const result = await api('GET', '/api/tasks?kind=pipeline&visibility=all&limit=20');
    const task = (result.tasks || []).find(item => _sameRunProject(item)
      && ['queued', 'running', 'cancelling'].includes(item.status));
    if (task) {
      _managedTaskId = task.id;
      renderManagedTaskLink();
      _handleRunStatus(_taskToRunStatus(task));
    }
  } catch { /* compatibility fallback handles older servers */ }
}

/** Re-fetch the video list so artifacts from a finished/cancelled/failed run show up. */
async function refreshVideosAfterRun() {
  try {
    await import('./sidebar.js').then(mod => mod.loadVideos());
  } catch { /* 刷新失败不阻断完成后的导航/提示 */ }
}

/** Stale-progress warning box; ASR configuration hint only for the transcribe phase. */
function staleWarningHtml(phase) {
  const settingsLink = `<a href="#" id="link-stale-settings" style="text-decoration:underline;color:var(--accent)">${t('shell.nav_config')}</a>`;
  const whisperHint = phase === 'transcribe'
    ? `<span style="color:var(--text-secondary)">${t('run.stale_settings_hint', { link: settingsLink })}</span>`
    : '';
  return `
    <div id="stale-warn" style="display:none;margin-top:8px;padding:8px;background:var(--warning-bg,#2a2520);border:1px solid var(--warning-border,#b8860b);border-radius:6px;font-size:var(--text-sm)">
      ${t('run.stale_hint')}<br>
      ${whisperHint}
    </div>
  `;
}

async function _showTranscriptionFailureNotice(prog) {
  try {
    const ps = await api('GET', '/api/processing-state');
    const hasTranscribeErr = Object.values(ps.files || {}).some((f) => f.transcribe === 'error');
    if (!hasTranscribeErr) return;

    let check = null;
    try { check = await api('GET', '/api/whisper/check'); } catch { /* 旧服务仍显示通用本地提示 */ }
    const notice = transcriptionFailureNotice(check);
    registerNotification({
      title: notice.title,
      message: t('run.transcribe_notice_plain', { message: notice.message, settings: notice.settingsLabel }),
      severity: 'error',
      sourceType: 'ui_status',
      sourceId: 'transcribe-failure',
      dedupeKey: `pipeline:transcribe-failure:${state.currentProjectDir || state.currentProjectName || 'global'}`,
      data: { action_id: 'go-settings-keys' },
    });

    const warn = document.createElement('div');
    warn.id = 'run-transcribe-warn';
    warn.style.cssText = 'margin-top:12px;padding:12px;background:var(--warning-bg,#2a2520);border:1px solid var(--warning-border,#b8860b);border-radius:6px';
    warn.innerHTML = `
      <p style="margin:0 0 8px;font-weight:600">❗ ${escapeHtml(notice.title)}</p>
      <p style="margin:0 0 8px;font-size:var(--text-sm);color:var(--text-secondary)">${t('run.transcribe_notice_link', { message: escapeHtml(notice.message), link: `<a href="#" id="link-go-settings" style="text-decoration:underline;color:var(--accent)">${escapeHtml(notice.settingsLabel)}</a>` })}</p>
    `;
    prog.appendChild(warn);
    const settingsLink = $('link-go-settings');
    if (settingsLink) {
      settingsLink.onclick = (e) => {
        e.preventDefault();
        import('./sidebar.js').then((mod) => mod.selectConfig());
      };
    }
  } catch { /* 通知失败不应阻断运行收尾 */ }
}

export async function _handleRunStatus(s) {
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
        prog.innerHTML = `<p class="muted">${t('run.no_run')}</p>`;
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
            <p class="warn">${t('run.stale_interrupted')}</p>
            <p><strong>${t('run.phase')}</strong> ${escapeHtml(s.phase || '')}</p>
            <p><strong>${t('run.progress')}</strong> ${s.current}/${s.total}</p>
            <p><strong>${t('run.state')}</strong> ${escapeHtml(s.message || '')}</p>
            ${logsHtml}
          `;
          renderProcessingState($('run-state-container'));
        }
      } else {
        _seenNonTerminal = true;
        if (btn) { btn.disabled = true; btn.textContent = t('run.btn.running'); }
        const cancelBtn = $('btn-run-cancel');
        if (cancelBtn) { cancelBtn.style.display = ''; cancelBtn.disabled = false; }
        if (hasRunDom) {
          const pct = s.total > 0 ? Math.round(s.current / s.total * 100) : 0;
          const eta = s.eta_sec ? t('run.eta', { sec: Math.round(s.eta_sec) }) : '';
          const logsHtml = s.logs?.length ? `<div class="run-logs">${s.logs.map(l => `<div class="run-log-line">${escapeHtml(l)}</div>`).join('')}</div>` : '';
          prog.innerHTML = `
            <p><strong>${t('run.phase')}</strong> ${escapeHtml(s.phase || '')}</p>
            <p><strong>${t('run.progress')}</strong> ${s.current}/${s.total} (${pct}%)${eta}</p>
            <p><strong>${t('run.state')}</strong> ${escapeHtml(s.message || '')}</p>
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
      updateRunStartButtonState();
      const cancelBtn = $('btn-run-cancel');
      if (cancelBtn) cancelBtn.style.display = 'none';
      if (hasRunDom) {
        const logsHtml = s.logs?.length ? `<div class="run-logs">${s.logs.map(l => `<div class="run-log-line">${escapeHtml(l)}</div>`).join('')}</div>` : '';
        prog.innerHTML = `<p class="ok">✓ ${t('run.done')}</p><p>${escapeHtml(s.message || '')}</p>${logsHtml}`;
      }
      setStatus(t('run.done'), 'ok', { persist: !_managedTaskId });
      addToast(s.message || t('run.done'), 'success', undefined, { persist: false });
      if (hasRunDom) renderProcessingState($('run-state-container'));
      if (hasRunDom) await _showTranscriptionFailureNotice(prog);
      state.currentDay = _lastRunDay;
      state.plan = null;
      await import('./sidebar.js').then(mod => mod.loadPlans());
      updateSidebarDay();
      import('./sidebar.js').then(mod => mod.renderVideoList());
      import('./sidebar.js').then(mod => mod.saveProject());
      try {
        const loaded = await apiWithMeta('GET', `/api/plan?day=${_lastRunDay}`);
        state.plan = loaded.data;
        setArtifactVersion('plan', _lastRunDay, loaded.version);
      } catch {}
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
      updateRunStartButtonState();
      const cancelBtn = $('btn-run-cancel');
      if (cancelBtn) cancelBtn.style.display = 'none';
      if (hasRunDom) {
        const logsHtml = s.logs?.length ? `<div class="run-logs">${s.logs.map(l => `<div class="run-log-line">${escapeHtml(l)}</div>`).join('')}</div>` : '';
        prog.innerHTML = `<p class="warn">⏹ ${t('run.cancelled')}</p><p>${escapeHtml(s.message || '')}</p>${logsHtml}`;
      }
      setStatus(t('run.cancelled'), 'warn', { persist: !_managedTaskId });
      addToast(s.message || t('run.cancelled'), 'warning', undefined, { persist: false });
      if (hasRunDom) renderProcessingState($('run-state-container'));
      await refreshVideosAfterRun();
    } else if (s.status === 'error') {
      _lastProgressSnapshot = null;
      _runActive = false;
      updateRunStartButtonState();
      const cancelBtn = $('btn-run-cancel');
      if (cancelBtn) cancelBtn.style.display = 'none';
      if (hasRunDom) {
        const logsHtml = s.logs?.length ? `<div class="run-logs">${s.logs.map(l => `<div class="run-log-line">${escapeHtml(l)}</div>`).join('')}</div>` : '';
        prog.innerHTML = `<p class="err">✗ ${t('run.error')}</p><p>${escapeHtml(s.message || '')}</p>${logsHtml}`;
      }
      setStatus(t('run.error'), 'err', { persist: !_managedTaskId });
      addToast(s.message || t('run.error'), 'error', 6000, { persist: false });
      if (hasRunDom) renderProcessingState($('run-state-container'));
      await refreshVideosAfterRun();
    }
}

function _completionTargetForSteps(steps) {
  const stepSet = new Set(Array.isArray(steps) ? steps : []);
  if (stepSet.has('plan') || stepSet.has('plan_voiceover')) return { entity: 'plan' };
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

// The Run pane is rendered from JS strings, so re-render it on a language switch
// instead of leaving stale copy behind. A live run keeps painting its own progress.
onLocaleChange(() => {
  if (!_runActive && state.currentEntity === 'run') renderRun();
});

export {
  renderRun,
  startRun,
  updateRunFilesBadge,
  updateRunStartButtonState,
  getRunButtonText,
  collectRunOptions,
  _completionTargetForSteps,
  _shouldResetRunNavigationOnRender,
  renderRunPreviewHtml,
  formatRunStartError,
  buildSkippedDiagnostics,
  renderSkippedDiagnosticsHtml,
  refreshVideosAfterRun,
  staleWarningHtml,
};
