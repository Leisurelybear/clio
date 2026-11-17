import { state } from './state.js';
import {
  $,
  escapeHtml,
  setStatus,
} from './utils.js';
import { api, icon } from './api.js';
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
  renderRunPreviewErrorHtml,
} from './runner-preview.js';
import { buildSkippedDiagnostics, renderProcessingState, renderSkippedDiagnosticsHtml, STEP_KEYS, stepShortLabel } from './runner-diagnostics.js';

import { buildRunStageModel, nextIncompleteStage, renderStageBoard, renderCompletionActions } from './runner-stages.js';
import { resolveRunPhase } from './runner-stage-model.js';
import { RunTaskSession } from './runner-task-session.js';

let _lastRunPreview = null;
let _customRun = false;
let _runLogs = [];
let _completedSteps = [];
let _activePhase = '';
let _previewFailed = false;

let _lastRunDay = 'day1';
let _runActive = false;
let _lastProgressSnapshot = null;
let _lastRunSteps = [];
let _managedTaskId = null;
let _taskEventsUnsubscribe = null;
const _taskSession = new RunTaskSession();
let _statusQueue = Promise.resolve();
let _lastRunAllDays = false;
let _guidedSkippedSteps = new Set();

function currentProject() { return state.currentProjectDir || state.config?.project_dir || ''; }

function syncRunProject() {
  if (_taskSession.resetProject(currentProject())) {
    _managedTaskId = null;
    _runActive = false;
    _runLogs = [];
    _activePhase = '';
    _completedSteps = [];
    _lastRunSteps = [];
    _lastRunAllDays = false;
    _lastProgressSnapshot = null;
    _statusQueue = Promise.resolve();
  }
}

const STEPS_KEY = 'vlog_ui_run_steps';
const SESSION_KEY = 'vlog_ui_run_session';

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

/**
 * Guided run session: which mode the page was in, which optional steps the
 * user skipped, and the processing scope. Keeping it per project means a
 * reload, a tab switch, or a task-center restore lands on the same next stage
 * instead of silently reverting to a full-pipeline run.
 */
function loadRunSession() {
  const project = currentProject();
  if (!project) return null;
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== 'object' || parsed.project !== project) return null;
    return parsed;
  } catch { return null; }
}

function saveRunSession() {
  const project = currentProject();
  // Unnamed projects stay in memory so one editor session cannot inherit
  // another project's skipped steps.
  if (!project) return;
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify({
      project,
      custom: _customRun,
      skipped: [..._guidedSkippedSteps],
      day: $('run-day')?.value.trim() || _lastRunDay || 'day1',
      all_days: !!$('run-all-days')?.checked,
    }));
  } catch { /* ignore */ }
}

function clearRunSession() {
  try { localStorage.removeItem(SESSION_KEY); } catch { /* ignore */ }
}

function renderRun() {
  syncRunProject();
  if (!_runActive) _lastRunDay = state.currentDay || 'day1';
  const pane = $('tab-run');
  _lastRunPreview = null;
  _previewFailed = false;
  const saved = loadStepSelection();
  const savedSteps = saved.steps || {};
  const session = loadRunSession();
  // Guided mode always starts from the complete user-facing workflow, but a
  // persisted session may restore an explicit skip or a custom selection.
  _customRun = Boolean(session?.custom);
  _guidedSkippedSteps = new Set(Array.isArray(session?.skipped) ? session.skipped : []);
  const sessionDay = typeof session?.day === 'string' ? session.day : '';
  if (sessionDay && !_runActive) _lastRunDay = sessionDay;
  const sessionAllDays = Boolean(session?.all_days);
  const savedUseTrans = saved.use_transcripts !== false;

  const stepChecks = RUN_STEPS.map(s => {
    const checked = _customRun ? savedSteps[s.key] !== false : !_guidedSkippedSteps.has(s.key);
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
            <input id="run-day" class="run-option-input" value="${escapeHtml(sessionDay || state.currentDay)}">
          </label>
          <label class="run-option run-option-check">
            <input type="checkbox" id="run-all-days" ${sessionAllDays ? 'checked' : ''}>
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
    <section class="run-scope-card">
      <div class="run-scope-heading">${t('run.scope.heading')}</div>
      <p class="hint">${t('run.scope.hint')}</p>
      <div id="run-scope"></div>
    </section>
    <section id="run-stage-board" class="run-stage-board"></section>
    <section class="run-next-card">
      <p id="run-next-description" class="hint"></p>
      <div id="run-next-blockers" class="run-next-blockers" hidden></div>
      <p id="run-next-estimate" class="hint"></p>
      <button id="btn-run-start" class="btn-primary">${getRunButtonText()}</button>
      <button id="btn-run-retry-preview" class="btn-secondary" hidden>${t('run.guide.retry_preview')}</button>
      <button id="btn-run-skip-transcribe" class="btn-secondary" hidden>${t('run.guide.skip_transcribe')}</button>
      <span id="run-files-badge" class="run-files-badge" style="display:none"></span>
      <button id="btn-run-cancel" class="btn-secondary" style="display:none">${t('common.cancel')}</button>
    </section>
    <details id="run-custom-steps" class="run-prompt-section"${_customRun ? ' open' : ''}>
      <summary>${t('run.guide.custom')}</summary>
      <p class="hint">${t('run.guide.custom_hint')}</p>
      <button id="btn-run-guided" class="btn-secondary">${t('run.guide.resume')}</button>
      <div class="run-step-list">${stepChecks}</div>
    </details>
    <details class="run-prompt-section" style="margin:12px 0">
      <summary style="cursor:pointer;font-size:var(--text-sm);color:var(--text-secondary);user-select:none">${t('run.guide.advanced')}</summary>
      <div style="margin-top:8px">
        <textarea id="run-context-override" class="run-prompt-input" placeholder="${t('run.context_placeholder')}" rows="4" style="width:100%;box-sizing:border-box;padding:8px;border:1px solid var(--border);border-radius:4px;background:var(--bg-input,#1e1e1e);color:var(--text-primary);font-size:var(--text-sm);resize:vertical;font-family:inherit"></textarea>
      </div>
    </details>
    <details id="run-execution-options" class="run-prompt-section">
      <summary>${t('run.guide.execution_options')}</summary>
      <label class="run-option run-option-check" id="option-overwrite-wrap">
        <input type="checkbox" id="run-overwrite"><span>${t('run.overwrite')}</span>
      </label>
      <p class="hint">${t('run.guide.overwrite_once')}</p>
      <div id="run-preview" style="margin-top:12px"></div>
    </details>
    <div id="run-progress" style="margin-top:12px"></div>
    <div id="run-completion-actions" aria-live="polite"></div>
    <div id="run-task-link" class="run-task-link" hidden></div>
    <div id="run-state-container"></div>
  `;
  const dayScope = pane.querySelector('.run-step-day-sub');
  if (dayScope) $('run-scope').appendChild(dayScope);
  $('btn-run-retry-preview').onclick = () => refreshRunPreview();
  $('btn-run-skip-transcribe').onclick = () => {
    if (_runActive) return;
    const transcribe = pane.querySelector('.run-step-cb[data-step="transcribe"]');
    if (!transcribe) return;
    _guidedSkippedSteps.add('transcribe');
    transcribe.checked = false;
    const checks = {};
    pane.querySelectorAll('.run-step-cb').forEach(cb => { checks[cb.dataset.step] = cb.checked; });
    saveStepSelection(checks, $('run-use-transcripts')?.checked ?? true);
    saveRunSession();
    return refreshRunPreview({ silent: true });
  };

  // wire step checkbox change → persist + refresh preview
  document.querySelectorAll('.run-step-cb').forEach(cb => {
    cb.addEventListener('change', () => {
      _customRun = true;
      const checks = {};
      document.querySelectorAll('.run-step-cb').forEach(c => {
        checks[c.dataset.step] = c.checked;
      });
      saveStepSelection(checks, $('run-use-transcripts')?.checked ?? true);
      saveRunSession();
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
      saveRunSession();
      toggleDayScopeInputs();
      refreshRunPreview({ silent: true });
    });
  }

  $('btn-run-guided').onclick = () => {
    _customRun = false;
    _guidedSkippedSteps = new Set();
    pane.querySelectorAll('.run-step-cb').forEach(cb => { cb.checked = true; });
    saveStepSelection(Object.fromEntries(RUN_STEPS.map(step => [step.key, true])), $('run-use-transcripts')?.checked ?? true);
    clearRunSession();
    saveRunSession();
    togglePlanSubOptions();
    updateRunGuide();
    refreshRunPreview({ silent: true });
  };
  $('run-day')?.addEventListener('input', () => {
    saveRunSession();
    refreshRunPreview({ silent: true });
  });
  $('run-context-override')?.addEventListener('change', () => refreshRunPreview({ silent: true }));
  togglePlanSubOptions();
  updateRunFilesBadge();
  updateRunStartButtonState();

  const runBtn = $('btn-run-start');
  runBtn.onclick = () => {
    const next = nextIncompleteStage(currentStageModel());
    return startRun(!_customRun && _lastRunPreview && next ? next.steps : undefined);
  };
  if (_runActive) { runBtn.disabled = true; runBtn.textContent = t('run.btn.running'); }
  const cancelBtn = $('btn-run-cancel');
  if (cancelBtn) cancelBtn.onclick = cancelRun;
  _subscribeManagedRunEvents();
  const task = _taskSession.task;
  if (task) {
    const project = currentProject();
    _statusQueue = _statusQueue.catch(() => {}).then(() => {
      if (project === currentProject() && task.id === _managedTaskId) {
        return _handleRunStatus(_taskToRunStatus(task), { restore: true });
      }
    });
  }
  _syncManagedRunTask();
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

function currentStageModel() {
  return buildRunStageModel({ selectedSteps: collectRunOptions().steps, preview: _lastRunPreview,
    activePhase: _activePhase, completedSteps: _completedSteps });
}

function guidedStepKeys() {
  return RUN_STEPS.map(step => step.key).filter(step => !_guidedSkippedSteps.has(step));
}

function updateRunGuide() {
  const board = $('run-stage-board');
  if (!board) return;
  const model = currentStageModel();
  renderStageBoard(board, model, stage => {
    if (stage?.output?.steps?.length) {
      _showRunCompletionTarget(stage.output.steps, _lastRunDay, $('run-all-days')?.checked, stage.output.target);
    }
  });
  const next = nextIncompleteStage(model, _lastRunPreview);
  const skipTranscribe = $('btn-run-skip-transcribe');
  if (skipTranscribe) {
    const row = _lastRunPreview?.steps?.find(step => step.name === 'transcribe');
    skipTranscribe.hidden = _customRun || next?.key !== 'source_prep' || !next.steps.includes('transcribe')
      || !row || row.disabled || !(Number(row.will_run) > 0 || Number(row.total) === 0);
    skipTranscribe.disabled = _runActive;
    skipTranscribe.title = t('run.guide.skip_transcribe_hint');
  }
  const retry = $('btn-run-retry-preview');
  if (retry) retry.hidden = !_previewFailed;
  const estimate = $('run-next-estimate');
  if (estimate) {
    const usage = next?.estimate;
    const calls = Number(usage?.planned_calls) || 0;
    const cost = Number(usage?.estimated_cost_usd) || 0;
    estimate.textContent = calls ? `${t('run.preview.ai_calls', { count: calls })} · ${usage.estimate_status === 'estimated'
      ? t('run.preview.ai_cost', { cost: `$${cost.toFixed(4)}` }) : t('run.preview.cost_unconfigured')}` : '';
  }
  const description = $('run-next-description');
  if (description) description.textContent = t(_customRun ? 'run.guide.custom_hint'
    : next ? 'run.guide.next_hint' : 'run.guide.all_current');
  const blockersEl = $('run-next-blockers');
  if (blockersEl) {
    const blockedReasons = !_customRun && next && ['blocked', 'unknown'].includes(next.status)
      ? [...new Set(next.blockers || [])]
      : [];
    blockersEl.hidden = blockedReasons.length === 0;
    blockersEl.innerHTML = blockedReasons.length
      ? `<small>${escapeHtml(t('run.guide.blockers_heading'))}</small><ul>${blockedReasons.map(reason => `<li>${escapeHtml(reason)}</li>`).join('')}</ul>`
      : '';
  }
  const btn = $('btn-run-start');
  if (btn && !_runActive && !_customRun && !_lastRunPreview) {
    btn.disabled = true;
    btn.textContent = t(_previewFailed ? 'run.guide.preview_failed' : 'run.preview.generating');
  }
  if (btn && !_runActive && !_customRun && _lastRunPreview) {
    btn.textContent = next ? t('run.guide.next', { stage: t(`run.stage.${next.key}`) }) : t('run.guide.all_current');
    btn.disabled = !next || ['blocked', 'unknown'].includes(next.status) || (state.selectionMode && !state.selectedFiles.length);
  }
}

function updateRunStartButtonState() {
  const btn = $('btn-run-start');
  if (!btn || _runActive) return;
  const noSelection = state.selectionMode && state.selectedFiles.length === 0;
  toggleDayScopeInputs();
  btn.disabled = noSelection;
  btn.innerHTML = getRunButtonText();
  btn.title = noSelection ? t('run.pick_first_title') : '';
  updateRunGuide();
}

function collectRunOptions() {
  const checkedSteps = [...document.querySelectorAll('.run-step-cb:checked')].map(cb => cb.dataset.step);
  // Unit callers and small embedded forms may not render the Run page shell;
  // in that case their checked boxes are the explicit custom selection.
  const guided = Boolean(document.getElementById('run-custom-steps')) && !_customRun;
  const steps = guided ? guidedStepKeys() : checkedSteps;
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

/** Retry button rendered inside the preview pane failure state. */
function _bindPreviewRetry(container) {
  container.querySelector('.run-preview-retry')?.addEventListener('click', () => refreshRunPreview());
}
async function refreshRunPreview({ silent = false } = {}) {
  const container = $('run-preview');
  if (!container) return null;
  const project = currentProject();
  const options = collectRunOptions();
  const ac = beginLatest('run-preview');
  _lastRunPreview = null;
  _previewFailed = false;
  updateRunStartButtonState();
  if (!options.steps.length) {
    _lastRunPreview = null;
    updateRunGuide();
    container.innerHTML = renderRunPreviewHtml(null);
    _bindPreviewRetry(container);
    endLatest('run-preview', ac);
    return null;
  }
  if (!silent) {
    container.innerHTML = `<p class="muted">${t('run.preview.generating')}</p>`;
  }
  try {
    const response = await api('POST', '/api/run/preview', options, { signal: ac.signal });
      if (!isLatest('run-preview', ac) || project !== currentProject()) return null;
    _lastRunPreview = response?.preview || null;
    _previewFailed = !_lastRunPreview;
    if (!_runActive) _completedSteps = [];
    updateRunStartButtonState();
    if (response?.preview) {
      container.innerHTML = renderRunPreviewHtml(response.preview);
      const p = response.preview;
      if (p.preflight?.ok === false || p.totals?.warnings > 0 || p.artifact_overwrite?.required
        || p.plan_overwrite?.required || p.analysis_rerun?.required || p.legacy_audio_removal?.required) {
        const details = $('run-execution-options');
        if (details) details.open = true;
      }
    } else {
      _previewFailed = true;
      container.innerHTML = renderRunPreviewErrorHtml();
      _bindPreviewRetry(container);
    }
  } catch (e) {
    if (isAbortError(e) || !isLatest('run-preview', ac) || project !== currentProject()) return null;
    _lastRunPreview = null;
    _previewFailed = true;
    updateRunStartButtonState();
    container.innerHTML = renderRunPreviewErrorHtml();
    _bindPreviewRetry(container);
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

async function startRun(stageSteps) {
  const btn = $('btn-run-start');
  if (btn.disabled) return;
  const project = currentProject();
  const stillCurrent = () => project === currentProject();
  if (state.selectionMode && state.selectedFiles.length === 0) {
    setStatus(t('run.pick_first_title'), 'warn');
    addToast(t('run.btn.pick_first'), 'warning', undefined, { persist: false });
    return;
  }
  btn.disabled = true;
  btn.textContent = t('run.starting');
  const options = collectRunOptions();
  if (Array.isArray(stageSteps)) options.steps = stageSteps;
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
      if (!stillCurrent()) return;
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
    if (!stillCurrent()) return;
    _lastRunDay = options.day_label;
    _lastRunAllDays = !!options.all_days;
    _lastRunSteps = options.steps.slice();
    const r = await api('POST', '/api/run/start', options);
    if (!stillCurrent()) return;
    if (r.ok) {
      _managedTaskId = r.task_id || r.task?.id || null;
      if (_taskSession.finalized.has(_managedTaskId)) return;
      renderManagedTaskLink();
      _runActive = true;
      _activePhase = '';
      _runLogs = [];
      _completedSteps = [];
      if ($('run-completion-actions')) $('run-completion-actions').innerHTML = '';
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
    if (!stillCurrent()) return;
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
  const mapped = status === 'succeeded' ? (Number(task.result_summary?.error_count) > 0 ? 'error' : 'done')
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
    message: mapped === 'error' && status === 'succeeded' ? t('run.incomplete')
      : ['failed', 'interrupted', 'succeeded', 'cancelled'].includes(status) ? task.error_message || task.message || ''
        : event?.event?.message || task?.message || '',
    task_id: task?.id,
    steps: task?.input_summary?.steps || _lastRunSteps,
    day_label: task?.input_summary?.day_label || task?.input_data?.day_label || _lastRunDay,
    all_days: !!(task?.input_summary?.all_days || task?.input_data?.all_days),
    project_id: task?.project_id,
    logs: _runLogs.slice(-20),
    eta_sec: task?.eta_sec,
  };
}

function _sameRunProject(task) {
  const project = currentProject();
  return !project || !task?.project_id || String(task.project_id) === String(project);
}

function receiveManagedTask(task, payload = null, attach = false) {
  syncRunProject();
  if (!_sameRunProject(task) || !_taskSession.accept(task, { seq: payload?.seq, attach: attach || task.id === _managedTaskId })) return;
  if (_managedTaskId !== task.id) {
    _runLogs = [];
    _activePhase = '';
    _completedSteps = [];
  }
  _managedTaskId = task.id;
  _lastRunDay = task.input_summary?.day_label || task.input_data?.day_label || _lastRunDay;
  _lastRunSteps = task.input_summary?.steps || task.input_data?.steps || [];
  _lastRunAllDays = !!(task.input_summary?.all_days || task.input_data?.all_days);
  const message = payload?.event?.message;
  if (message) _runLogs = [..._runLogs, String(message)].slice(-20);
  const status = _taskToRunStatus(task, payload);
  renderManagedTaskLink();
  // Await each finalizer; a snapshot and a stream event may arrive in either order.
  _statusQueue = _statusQueue.catch(() => {}).then(() => {
    if (task.id !== _managedTaskId || !_sameRunProject(task)) return;
    return _handleRunStatus(status);
  });
}

function _subscribeManagedRunEvents() {
  if (_taskEventsUnsubscribe) return;
  _taskEventsUnsubscribe = subscribeTaskEvents(payload => {
    const task = payload?.task;
    if (!task || task.kind !== 'pipeline' || !_sameRunProject(task)) return;
    receiveManagedTask(task, payload);
  });
}

async function _syncManagedTaskSnapshot(taskId) {
  try {
    const task = await fetchTask(taskId);
    if (!task || task.id !== _managedTaskId || task.kind !== 'pipeline') return;
    receiveManagedTask(task, null, true);
  } catch { /* stream remains the primary source */ }
}

async function _syncManagedRunTask() {
  const project = currentProject();
  const taskAtRequest = _managedTaskId;
  try {
    const result = await api('GET', '/api/tasks?kind=pipeline&visibility=all&limit=20');
    if (project !== currentProject() || taskAtRequest !== _managedTaskId) return;
    const tasks = (result.tasks || []).filter(item => _sameRunProject(item));
    const task = tasks.find(item => ['queued', 'running', 'cancelling'].includes(item.status)) || tasks[0];
    if (task) {
      receiveManagedTask(task, null, true);
    }
  } catch { /* compatibility fallback handles older servers */ }
}

/** Re-fetch the video list so artifacts from a finished/cancelled/failed run show up. */
async function refreshVideosAfterRun(stillCurrent = () => true) {
  try {
    const sidebar = await import('./sidebar.js');
    if (stillCurrent()) await sidebar.loadVideos();
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

export async function _handleRunStatus(s, { restore = false } = {}) {
  if (s.project_id && !_sameRunProject({ project_id: s.project_id })) return;
  if (!restore && ['done', 'error', 'cancelled'].includes(s.status) && !_taskSession.finalize(s.task_id)) return;
  const project = currentProject();
  const taskId = s.task_id;
  const stillCurrent = () => project === currentProject() && (!taskId || taskId === _managedTaskId);
  const completedSteps = Array.isArray(s.steps) ? s.steps : _lastRunSteps;
  const completedDay = s.day_label || _lastRunDay;
  const completedAllDays = s.all_days ?? _lastRunAllDays;
  const prog = $('run-progress');
  const btn = $('btn-run-start');
  // Terminal outcomes still refresh artifact lists and notify when Run is unmounted.
  const hasRunDom = !!prog;
  if (s.rerun) return;
    if (s.status === 'idle' || s.status === 'unknown') {
      _activePhase = '';
      _completedSteps = [];
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
      _runActive = !!s.running;
      if ($('run-completion-actions')) $('run-completion-actions').innerHTML = '';
      _activePhase = resolveRunPhase(s.phase, s.steps || _lastRunSteps, _activePhase);
      updateRunGuide();
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
        if (btn) { btn.disabled = true; btn.textContent = t('run.btn.running'); }
        const cancelBtn = $('btn-run-cancel');
        if (cancelBtn) { cancelBtn.style.display = ''; cancelBtn.disabled = false; }
        if (hasRunDom) {
          const pct = Math.max(0, Math.min(100, Number.isFinite(s.progress_pct) ? s.progress_pct : s.total > 0 ? Math.round(s.current / s.total * 100) : 0));
          const eta = s.eta_sec ? t('run.eta', { sec: Math.round(s.eta_sec) }) : '';
          const logsHtml = s.logs?.length ? `<div class="run-logs">${s.logs.map(l => `<div class="run-log-line">${escapeHtml(l)}</div>`).join('')}</div>` : '';
          prog.innerHTML = `
            <p><strong>${t('run.phase')}</strong> ${escapeHtml(s.phase || '')}</p>
            <p><strong>${t('run.progress')}</strong> ${s.current}/${s.total} (${pct}%)${eta}</p>
            <p><strong>${t('run.state')}</strong> ${escapeHtml(s.message || '')}</p>
            <div style="background:var(--bg-surface-3);border-radius:3px;height:8px;margin:8px 0">
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
      if (!restore) {
        setStatus(t('run.done'), 'ok', { persist: !_managedTaskId });
        addToast(s.message || t('run.done'), 'success', undefined, { persist: false });
        if (hasRunDom) renderProcessingState($('run-state-container'));
        if (hasRunDom && completedSteps.includes('transcribe')) await _showTranscriptionFailureNotice(prog);
        if (!stillCurrent()) return;
        const sidebar = await import('./sidebar.js');
        if (!stillCurrent()) return;
        await sidebar.loadPlans();
        if (!stillCurrent()) return;
        await refreshVideosAfterRun(stillCurrent);
      }
      if (!stillCurrent()) return;
      _activePhase = '';
      _completedSteps = completedSteps;
      if (hasRunDom) {
        // Overwrite applies to this submitted action, not the next guided stage.
        const overwrite = $('run-overwrite');
        if (overwrite) overwrite.checked = false;
        const outputPath = state.config?.output_dir || '';
        renderCompletionActions($('run-completion-actions'), completedSteps,
          () => _showRunCompletionTarget(completedSteps, completedDay, completedAllDays),
          async () => {
            try {
              const result = await api('POST', '/api/fs/reveal', { path: outputPath });
              if (!result.ok) throw new Error(result.error || t('project.open_failed_bare'));
            } catch (error) { setStatus(t('project.reveal_failed', { detail: error.message }), 'err'); }
          }, outputPath);
        updateRunGuide();
        if (!restore) await refreshRunPreview({ silent: true });
      }
    } else if (s.status === 'cancelled') {
      _activePhase = '';
      _lastProgressSnapshot = null;
      _runActive = false;
      updateRunStartButtonState();
      const cancelBtn = $('btn-run-cancel');
      if (cancelBtn) cancelBtn.style.display = 'none';
      if (hasRunDom) {
        const logsHtml = s.logs?.length ? `<div class="run-logs">${s.logs.map(l => `<div class="run-log-line">${escapeHtml(l)}</div>`).join('')}</div>` : '';
        prog.innerHTML = `<p class="warn">⏹ ${t('run.cancelled')}</p><p>${escapeHtml(s.message || '')}</p>${logsHtml}`;
      }
      if (!restore) {
        setStatus(t('run.cancelled'), 'warn', { persist: !_managedTaskId });
        addToast(s.message || t('run.cancelled'), 'warning', undefined, { persist: false });
        if (hasRunDom) renderProcessingState($('run-state-container'));
        await refreshVideosAfterRun(stillCurrent);
      }
      if (!restore && stillCurrent() && hasRunDom) {
        if ($('run-overwrite')) $('run-overwrite').checked = false;
        await refreshRunPreview({ silent: true });
      }
    } else if (s.status === 'error') {
      _activePhase = '';
      _lastProgressSnapshot = null;
      _runActive = false;
      updateRunStartButtonState();
      const cancelBtn = $('btn-run-cancel');
      if (cancelBtn) cancelBtn.style.display = 'none';
      if (hasRunDom) {
        const logsHtml = s.logs?.length ? `<div class="run-logs">${s.logs.map(l => `<div class="run-log-line">${escapeHtml(l)}</div>`).join('')}</div>` : '';
        prog.innerHTML = `<p class="err">✗ ${t('run.error')}</p><p>${escapeHtml(s.message || '')}</p>${logsHtml}`;
      }
      if (!restore) {
        setStatus(t('run.error'), 'err', { persist: !_managedTaskId });
        addToast(s.message || t('run.error'), 'error', 6000, { persist: false });
        if (hasRunDom) renderProcessingState($('run-state-container'));
        await refreshVideosAfterRun(stillCurrent);
      }
      if (!restore && stillCurrent() && hasRunDom) {
        if ($('run-overwrite')) $('run-overwrite').checked = false;
        await refreshRunPreview({ silent: true });
      }
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

async function _showRunCompletionTarget(steps, day = _lastRunDay, allDays = false, outputTarget = '') {
  const target = outputTarget ? {
    entity: outputTarget === 'plan' ? 'plan' : 'video',
    tab: outputTarget === 'voiceover' ? 'voiceover' : 'texts',
  } : _completionTargetForSteps(steps);
  if (!target) return;
  const sidebar = await import('./sidebar.js');
  if (target.entity === 'plan') {
    const targetDay = allDays ? state.availablePlans?.[0]?.day_label || day : day;
    await sidebar.selectPlan(targetDay);
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
  renderRunPreviewHtml,
  formatRunStartError,
  buildSkippedDiagnostics,
  renderSkippedDiagnosticsHtml,
  refreshVideosAfterRun,
  staleWarningHtml,
};
