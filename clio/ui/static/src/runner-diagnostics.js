import { hasKey, t } from './i18n.js';
import { escapeHtml } from './utils.js';
import { api } from './api.js';

/** Step order used by the processing-state table and the skipped diagnostics. */
export const STEP_KEYS = ['compress', 'transcribe', 'analyze', 'voiceover', 'plan', 'label'];

export function stepShortLabel(step) {
  const key = `run.step_short.${step}`;
  return hasKey(key) ? t(key) : step;
}

export function skipReasonHint(step) {
  const key = `run.skip_hint.${step}`;
  return hasKey(key) ? t(key) : t('run.skip_hint.fallback');
}

export function buildSkippedDiagnostics(processingState) {
  const files = processingState?.files || {};
  const stateSteps = Array.isArray(processingState?.steps) ? processingState.steps : STEP_KEYS;
  const stepKeys = stateSteps.filter(step => hasKey(`run.step_short.${step}`));
  const diagnostics = [];
  for (const [file, steps] of Object.entries(files).sort((a, b) => a[0].localeCompare(b[0]))) {
    if (!steps || typeof steps !== 'object') continue;
    for (const step of stepKeys) {
      if (steps[step] !== 'skipped') continue;
      diagnostics.push({
        file,
        step,
        label: stepShortLabel(step),
        reason: skipReasonHint(step),
      });
    }
  }
  return diagnostics;
}

export function renderSkippedDiagnosticsHtml(diagnostics) {
  const rows = (diagnostics || []).map(item => `
    <div class="skip-row">
      <span class="skip-file">${escapeHtml(item.file)}</span>
      <span class="skip-step">${escapeHtml(item.label || item.step)}</span>
      <span class="skip-reason">${escapeHtml(item.reason)}</span>
    </div>
  `).join('');
  return `
    <details class="skip-panel" ${rows ? 'open' : ''}>
      <summary>${t('run.skipped.summary')}</summary>
      <p class="muted">${t('run.skipped.hint')}</p>
      ${rows ? `<div class="skip-table">${rows}</div>` : `<p class="muted">${t('run.skipped.empty')}</p>`}
    </details>
  `;
}

const STATUS_ICON = { done: '✅', skipped: '⏭️', error: '✗', cancelled: '⏹', running: '…' };

export async function renderProcessingState(container) {
  try {
    const st = await api('GET', '/api/processing-state');
    const files = st.files;
    const stepKeys = STEP_KEYS;
    const entries = Object.entries(files).sort((a, b) => a[0].localeCompare(b[0]));
    if (!entries.length) { if (container) container.innerHTML = ''; return; }
    let html = `<h4 style="margin:12px 0 4px">${t('run.processing_state')}</h4><div class="state-table"><div class="state-row state-header"><span class="state-file">${t('run.table.file')}</span>`;
    for (const k of stepKeys) html += `<span class="state-cell">${stepShortLabel(k)}</span>`;
    html += '</div>';
    for (const [file, steps] of entries) {
      html += `<div class="state-row"><span class="state-file">${escapeHtml(file)}</span>`;
      for (const k of stepKeys) {
        const v = steps[k];
        html += `<span class="state-cell">${v ? STATUS_ICON[v] || v : ''}</span>`;
      }
      html += '</div>';
    }
    html += '</div>';
    html += renderSkippedDiagnosticsHtml(buildSkippedDiagnostics(st));
    if (container) container.innerHTML = html;
  } catch { /* ignore */ }
}
