/**
 * Token usage pane rendering.
 */
import { api } from './api.js';
import { t } from './i18n.js';
import { $, escapeHtml, setStatus } from './utils.js';

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
    const fmtCost = (value) => `$${Number(value || 0).toFixed(6)}`;
    const totalHtml = `
      <div style="display:flex;gap:var(--space-3);margin-bottom:var(--space-3);flex-wrap:wrap">
        <div class="token-card"><div class="token-card-value">${totals.total_tokens.toLocaleString()}</div><div class="token-card-label">${t('tokens.total_label')}</div></div>
        <div class="token-card"><div class="token-card-value">${totals.prompt_tokens.toLocaleString()}</div><div class="token-card-label">Prompt</div></div>
        <div class="token-card"><div class="token-card-value">${totals.completion_tokens.toLocaleString()}</div><div class="token-card-label">Completion</div></div>
        <div class="token-card"><div class="token-card-value">${fmtCost(totals.cost_usd)}</div><div class="token-card-label">${t('tokens.total_cost')}</div></div>
      </div>`;

    let modelHtml = `<h4 style="margin:var(--space-2) 0">${t('tokens.by_model')}</h4><table class="token-table"><tr><th>${t('tokens.col.model')}</th><th>${t('tokens.col.calls')}</th><th>Prompt</th><th>Completion</th><th>${t('tokens.col.total')}</th><th>${t('tokens.col.cost')}</th></tr>`;
    for (const [model, m] of Object.entries(data.by_model || {})) {
      modelHtml += `<tr><td>${escapeHtml(model)}</td><td>${m.calls}</td><td>${m.prompt_tokens.toLocaleString()}</td><td>${m.completion_tokens.toLocaleString()}</td><td>${m.total_tokens.toLocaleString()}</td><td>${fmtCost(m.cost_usd)}</td></tr>`;
    }
    modelHtml += '</table>';

    let taskHtml = `<h4 style="margin:var(--space-2) 0">${t('tokens.by_task')}</h4><table class="token-table"><tr><th>${t('tokens.col.task')}</th><th>${t('tokens.col.calls')}</th><th>Prompt</th><th>Completion</th><th>${t('tokens.col.total')}</th><th>${t('tokens.col.cost')}</th></tr>`;
    for (const [task, tk] of Object.entries(data.by_task || {})) {
      taskHtml += `<tr><td>${escapeHtml(task)}</td><td>${tk.calls}</td><td>${tk.prompt_tokens.toLocaleString()}</td><td>${tk.completion_tokens.toLocaleString()}</td><td>${tk.total_tokens.toLocaleString()}</td><td>${fmtCost(tk.cost_usd)}</td></tr>`;
    }
    taskHtml += '</table>';

    let historyHtml = `<h4 style="margin:var(--space-2) 0">${t('tokens.history')}</h4><table class="token-table"><tr><th>${t('tokens.col.time')}</th><th>${t('tokens.col.task')}</th><th>${t('tokens.col.provider')}</th><th>${t('tokens.col.model')}</th><th>${t('tokens.col.prompt_version')}</th><th>Prompt</th><th>Completion</th><th>${t('tokens.col.total')}</th><th>${t('tokens.col.cost')}</th></tr>`;
    for (const h of (data.history || []).slice().reverse().slice(0, 100)) {
      historyHtml += `<tr><td class="token-time">${escapeHtml(h.timestamp || '')}</td><td>${escapeHtml(h.task || '')}</td><td>${escapeHtml(h.provider || '')}</td><td>${escapeHtml(h.model || '')}</td><td>${escapeHtml(h.prompt_version || '')}</td><td>${(h.prompt_tokens || 0).toLocaleString()}</td><td>${(h.completion_tokens || 0).toLocaleString()}</td><td>${(h.total_tokens || 0).toLocaleString()}</td><td>${fmtCost(h.cost_usd)}</td></tr>`;
    }
    historyHtml += '</table>';

    pane.innerHTML = `<div style="padding:var(--space-2)">${totalHtml}${modelHtml}${taskHtml}${historyHtml}</div>`;
  } catch (e) {
    const message = t('tokens.load_failed', { detail: e.message || e });
    pane.innerHTML = `<p class="muted">${escapeHtml(message)}</p>`;
    setStatus(message, 'err');
  }
}
