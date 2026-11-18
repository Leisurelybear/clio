import { escapeHtml } from './utils.js';
import { t } from './i18n.js';

function safeNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, number) : 0;
}

function formatNumber(value) {
  return Math.trunc(safeNumber(value)).toLocaleString('en-US');
}

export function formatCost(value) {
  const cost = safeNumber(value);
  const decimals = cost >= 1 ? 2 : cost >= 0.01 ? 4 : 6;
  return `$${cost.toFixed(decimals)}`;
}

function formatByteSize(value) {
  const bytes = Math.trunc(safeNumber(value));
  if (bytes < 1024) {
    return `${formatNumber(bytes)} B`;
  }
  const units = ['KB', 'MB', 'GB', 'TB'];
  let amount = bytes / 1024;
  let unitIndex = 0;
  while (amount >= 1024 && unitIndex < units.length - 1) {
    amount /= 1024;
    unitIndex += 1;
  }
  const formatted = amount.toFixed(2).replace(/\.?0+$/, '');
  return `${formatted} ${units[unitIndex]}`;
}

function numericRange(minValue, maxValue) {
  const min = Math.trunc(safeNumber(minValue));
  const max = Math.max(min, Math.trunc(safeNumber(maxValue ?? minValue)));
  return { min, max, isRange: max !== min };
}

function stepNamesText(names) {
  const list = (Array.isArray(names) ? names : [])
    .map(name => t(`run.step_short.${String(name)}`))
    .join(t('common.list_separator'));
  return list;
}

function renderCountSpan(key, singleKey, minValue, maxValue) {
  const range = numericRange(minValue, maxValue);
  return range.isRange
    ? `<span>${escapeHtml(t(key, { min: formatNumber(range.min), max: formatNumber(range.max) }))}</span>`
    : `<span>${escapeHtml(t(singleKey, { count: formatNumber(range.min) }))}</span>`;
}

function renderStepCounts(step) {
  if (step.deferred) {
    return `<span class="run-preview-waiting">${escapeHtml(t('run.preview.waiting', {
      steps: stepNamesText(step.waiting_on),
    }))}</span>`;
  }
  return [
    renderCountSpan('run.preview.will_run_range', 'run.preview.will_run', step.will_run, step.will_run_max),
    renderCountSpan('run.preview.will_skip_range', 'run.preview.will_skip', step.will_skip_min, step.will_skip),
  ].join('');
}

function renderCascadeLine(step) {
  const cascadeFrom = Array.isArray(step.cascade_from) ? step.cascade_from : [];
  if (!cascadeFrom.length) return '';
  return `<div class="run-preview-cascade">${escapeHtml(t('run.preview.cascade_from', {
    steps: stepNamesText(cascadeFrom),
  }))}</div>`;
}

function renderUsageMetrics(usage) {
  const plannedCalls = Math.trunc(safeNumber(usage?.planned_calls));
  if (plannedCalls <= 0) {
    return '';
  }
  const status = String(usage?.estimate_status || 'unknown');
  const tokenCount = safeNumber(usage?.estimated_prompt_tokens)
    + safeNumber(usage?.estimated_completion_tokens);
  const cost = status === 'estimated'
    ? formatCost(usage?.estimated_cost_usd)
    : t('run.preview.cost_unconfigured');
  return [
    `<span class="run-preview-ai-calls">${escapeHtml(t('run.preview.ai_calls', { count: formatNumber(plannedCalls) }))}</span>`,
    `<span class="run-preview-ai-tokens">${escapeHtml(t('run.preview.ai_tokens', { count: formatNumber(tokenCount) }))}</span>`,
    `<span class="run-preview-ai-cost">${escapeHtml(t('run.preview.ai_cost', { cost: escapeHtml(cost) }))}</span>`,
    `<span class="run-preview-ai-upload">${escapeHtml(t('run.preview.upload', {
      size: escapeHtml(formatByteSize(usage?.estimated_upload_bytes)),
    }))}</span>`,
  ].join('');
}

export function needsPlanOverwriteConfirmation(preview, options) {
  return Boolean(
    options?.files
    && Array.isArray(options.steps)
    && options.steps.includes('plan')
    && preview?.plan_overwrite?.required,
  );
}

export function needsLegacyAudioRemovalConfirmation(preview, options) {
  return Boolean(
    Array.isArray(options?.steps)
    && preview?.legacy_audio_removal?.required,
  );
}

export function needsAnalysisRerunConfirmation(preview, options) {
  return Boolean(
    Array.isArray(options?.steps)
    && options.steps.includes('analyze')
    && preview?.analysis_rerun?.required,
  );
}

export function needsArtifactOverwriteConfirmation(preview, options) {
  return Boolean(
    Array.isArray(options?.steps)
    && (options.steps.includes('plan')
      || options.steps.includes('voiceover')
      || options.steps.includes('plan_voiceover'))
    && preview?.artifact_overwrite?.required,
  );
}

/**
 * Failure state shown inside the preview pane so the user sees why a preview
 * failed next to the retry action, instead of a contradictory empty prompt.
 */
export function renderRunPreviewErrorHtml() {
  return `
    <section class="run-preview-box run-preview-error">
      <h4 style="margin:0 0 6px">${t('run.preview.title')}</h4>
      <p class="err">${t('run.preview.failed_detail')}</p>
      <button class="btn-secondary run-preview-retry">${t('run.guide.retry_preview')}</button>
    </section>
  `;
}

export function renderRunPreviewHtml(preview) {
  if (!preview) {
    return `<p class="muted">${t('run.preview.empty')}</p>`;
  }
  const input = preview.input || {};
  const totals = preview.totals || {};
  const steps = Array.isArray(preview.steps) ? preview.steps : [];
  const preflight = preview.preflight;
  const missing = Array.isArray(preflight?.missing) ? preflight.missing : [];
  const separator = t('common.list_separator');
  const planOverwrite = preview.plan_overwrite?.required
    ? `<p class="warn run-plan-overwrite-warning">${t('run.preview.plan_overwrite', {
      day: escapeHtml(preview.plan_overwrite.day_label || ''),
    })}</p>`
    : '';
  const artifactOverwrite = preview.artifact_overwrite?.required
    ? `<p class="warn run-artifact-overwrite-warning">${t('run.preview.artifact_overwrite', {
      items: escapeHtml((preview.artifact_overwrite.items || []).join(separator)),
    })}</p>`
    : '';
  const legacyAudioRemoval = preview.legacy_audio_removal?.required
    ? `<p class="warn run-legacy-audio-removal-warning">${t('run.preview.legacy_audio_removal', {
      analyze_count: Number(preview.legacy_audio_removal.analyze_count || 0),
      voiceover_count: Number(preview.legacy_audio_removal.voiceover_count || 0),
      compress_count: Number(preview.legacy_audio_removal.compress_count || 0),
    })}</p>`
    : '';
  const analysisReasons = Array.isArray(preview.analysis_rerun?.reasons)
    ? preview.analysis_rerun.reasons
    : [];
  const analysisRerun = preview.analysis_rerun?.required
    ? `<div class="warn run-analysis-rerun-warning">
      <p>${t('run.preview.analysis_rerun', {
        count: Number(preview.analysis_rerun.count || 0),
      })}</p>
      ${analysisReasons.length ? `<ul>${analysisReasons.map(reason => {
        const key = String(reason?.key || 'unknown');
        return `<li>${escapeHtml(t(`run.preview.analysis_reason.${key}`))} (${Number(reason?.count || 0)})</li>`;
      }).join('')}</ul>` : ''}
    </div>`
    : '';
  const preflightLine = preflight && preflight.ok === false
    ? `<p class="err run-preflight-error">${t('run.preview.preflight_error', { detail: escapeHtml(preflight.detail || t('run.preview.step_unrunnable')) })}\n` +
      `${missing.length ? t('run.preview.missing', { items: escapeHtml(missing.join(separator)) }) : ''}</p>`
    : '';
  const stepRows = steps.map(step => {
    const warnings = (step.warnings || []).map(w => `<div class="warn">${escapeHtml(w)}</div>`).join('');
    return `
      <div class="run-preview-step ${step.deferred ? 'run-preview-step-waiting' : ''}">
        <span class="run-preview-name">${escapeHtml(step.label || step.name || '')}</span>
        <span>${t('run.preview.total', { count: Number(step.total || 0) })}</span>
        ${renderStepCounts(step)}
        ${renderUsageMetrics(step)}
        ${renderCascadeLine(step)}
        ${warnings}
      </div>
    `;
  }).join('');
  const warningLine = Number(totals.warnings || 0) > 0
    ? `<p class="warn">${t('run.preview.warnings', { count: Number(totals.warnings || 0) })}</p>`
    : '';
  const minimumEstimate = Boolean(totals.estimate_is_minimum);
  const deferredLine = Number(totals.deferred_steps || 0) > 0
    ? `<p class="muted run-preview-deferred-note">${t('run.preview.deferred_steps', {
      count: Number(totals.deferred_steps || 0),
    })}</p>`
    : '';
  return `
    <section class="run-preview-box">
      <h4 style="margin:0 0 6px">${t('run.preview.title')}</h4>
      <p class="muted">${t('run.preview.input', { path: escapeHtml(input.path || ''), count: Number(input.count || 0) })}</p>
      <div class="run-preview-totals">
        <span>${t('run.preview.steps', { count: Number(totals.selected_steps || 0) })}</span>
        ${renderCountSpan('run.preview.will_run_range', 'run.preview.will_run', totals.will_run, totals.will_run_max)}
        ${renderCountSpan('run.preview.will_skip_range', 'run.preview.will_skip', totals.will_skip_min, totals.will_skip)}
        ${renderUsageMetrics(totals)}
      </div>
      ${Number(totals.planned_calls || 0) > 0 || minimumEstimate
        ? `<p class="muted run-preview-estimate-note">${t(minimumEstimate
          ? 'run.preview.estimate_note_minimum' : 'run.preview.estimate_note')}</p>`
        : ''}
      ${deferredLine}
      ${preflightLine}
      ${warningLine}
      ${planOverwrite}
      ${artifactOverwrite}
      ${legacyAudioRemoval}
      ${analysisRerun}
      <div class="run-preview-steps">${stepRows}</div>
    </section>
  `;
}

export function formatRunStartError(error) {
  const preflight = error?.body?.code === 'media_dependency_missing' ? error.body.preflight : null;
  const detail = preflight?.detail || error?.body?.error || error?.message || t('run.start_failed');
  const missing = Array.isArray(preflight?.missing) && preflight.missing.length
    ? t('run.start_failed_missing', { items: preflight.missing.join(t('common.list_separator')) })
    : '';
  return `${detail}${missing}`;
}
