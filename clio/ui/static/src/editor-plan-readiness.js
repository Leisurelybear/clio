import { state } from './state.js';
import { $, escapeHtml } from './utils.js';
import { api } from './api.js';
import { beginLatest, endLatest, isLatest, isAbortError } from './latest.js';
import { t } from './i18n.js';

let _readinessTimer = null;
let _lastReadiness = { ok: true, errors: [], warnings: [] };
let _onIssueClick = null;

export function configurePlanReadiness(options = {}) {
  _onIssueClick = typeof options.onIssueClick === 'function' ? options.onIssueClick : null;
}

export function getPlanReadiness() {
  return _lastReadiness;
}

export function schedulePlanReadinessCheck() {
  if (_readinessTimer) clearTimeout(_readinessTimer);
  _readinessTimer = setTimeout(() => {
    _readinessTimer = null;
    refreshPlanReadiness();
  }, 400);
}

export async function refreshPlanReadiness() {
  const panel = $('plan-readiness');
  if (!panel || !state.plan) return;
  const ac = beginLatest('plan-readiness');
  panel.innerHTML = `<p class="muted">${t('plan.readiness_checking')}</p>`;
  try {
    const r = await api('POST', '/api/plan/readiness', {
      day: state.currentDay || 'day1',
      source: $('cut-source')?.value || 'original',
      plan: state.plan,
    }, { signal: ac.signal });
    if (!isLatest('plan-readiness', ac)) return;
    const errors = r.errors || [];
    const warnings = r.warnings || [];
    _lastReadiness = { ok: !!r.ok, errors, warnings };
    renderReadinessList(panel, errors, warnings);
    updatePlanActionButtonsGate();
  } catch (e) {
    if (isAbortError(e) || !isLatest('plan-readiness', ac)) return;
    panel.innerHTML = `<p class="err">${t('plan.readiness_failed', { detail: escapeHtml(e.message || e) })}</p>`;
    _lastReadiness = { ok: false, errors: [{ message: String(e.message || e) }], warnings: [] };
    updatePlanActionButtonsGate();
  } finally {
    endLatest('plan-readiness', ac);
  }
}

export function updatePlanActionButtonsGate() {
  const hasErrors = (_lastReadiness.errors || []).length > 0;
  const cutBtn = $('btn-cut-exec');
  const expBtn = $('btn-jianying-export');
  if (cutBtn) {
    cutBtn.disabled = hasErrors;
    cutBtn.title = hasErrors ? t('plan.fix_errors_first') : '';
  }
  if (expBtn) {
    expBtn.disabled = hasErrors;
    expBtn.title = hasErrors ? t('plan.fix_errors_first') : '';
  }
}

function renderReadinessList(panel, errors, warnings) {
  if (!errors.length && !warnings.length) {
    panel.innerHTML = `<p class="ok">${t('plan.readiness_ok')}</p>`;
    return;
  }
  const parts = [`<h3>${t('plan.readiness_title')}</h3>`];
  if (errors.length) {
    parts.push('<ul class="plan-readiness-errors">');
    for (const issue of errors) {
      const idx = issue.segment_index;
      parts.push(
        `<li class="plan-issue-error" data-seg="${idx != null ? idx : ''}">${escapeHtml(issue.message || issue.code || t('plan.issue_error'))}</li>`
      );
    }
    parts.push('</ul>');
  }
  if (warnings.length) {
    parts.push('<ul class="plan-readiness-warnings">');
    for (const issue of warnings) {
      const idx = issue.segment_index;
      parts.push(
        `<li class="plan-issue-warn" data-seg="${idx != null ? idx : ''}">${escapeHtml(issue.message || issue.code || t('plan.issue_warning'))}</li>`
      );
    }
    parts.push('</ul>');
  }
  panel.innerHTML = parts.join('');
  panel.querySelectorAll('[data-seg]').forEach((el) => {
    el.onclick = () => {
      const raw = el.getAttribute('data-seg');
      if (raw === '' || raw == null) return;
      const idx = parseInt(raw, 10);
      if (!Number.isFinite(idx) || idx < 0) return;
      _onIssueClick?.(idx);
    };
  });
}