import { escapeHtml } from './utils.js';
import { t } from './i18n.js';

function safeNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, number) : 0;
}

function formatNumber(value) {
  return Math.trunc(safeNumber(value)).toLocaleString('en-US');
}

function formatCost(value) {
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
      <div class="run-preview-step">
        <span class="run-preview-name">${escapeHtml(step.label || step.name || '')}</span>
        <span>${t('run.preview.total', { count: Number(step.total || 0) })}</span>
        <span>${t('run.preview.will_run', { count: Number(step.will_run || 0) })}</span>
        <span>${t('run.preview.will_skip', { count: Number(step.will_skip || 0) })}</span>
        ${renderUsageMetrics(step)}
        ${warnings}
      </div>
    `;
  }).join('');
  const warningLine = Number(totals.warnings || 0) > 0
    ? `<p class="warn">${t('run.preview.warnings', { count: Number(totals.warnings || 0) })}</p>`
    : '';
  return `
    <section class="run-preview-box">
      <h4 style="margin:0 0 6px">${t('run.preview.title')}</h4>
      <p class="muted">${t('run.preview.input', { path: escapeHtml(input.path || ''), count: Number(input.count || 0) })}</p>
      <div class="run-preview-totals">
        <span>${t('run.preview.steps', { count: Number(totals.selected_steps || 0) })}</span>
        <span>${t('run.preview.will_run', { count: Number(totals.will_run || 0) })}</span>
        <span>${t('run.preview.will_skip', { count: Number(totals.will_skip || 0) })}</span>
        ${renderUsageMetrics(totals)}
      </div>
      ${Number(totals.planned_calls || 0) > 0
        ? `<p class="muted run-preview-estimate-note">${t('run.preview.estimate_note')}</p>`
        : ''}
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