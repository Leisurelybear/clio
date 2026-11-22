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
    const fmtCost = (value) => {
      const number = Number(value);
      return `$${(Number.isFinite(number) ? number : 0).toFixed(6)}`;
    };
    const callCount = (bucket) => {
      const number = Number(bucket?.calls);
      return Number.isFinite(number) && number >= 0 ? number : 0;
    };
    const totalCalls = Object.values(data.by_model || {}).reduce((sum, bucket) => sum + callCount(bucket), 0);
    const costValue = (bucket, fallbackCalls = 0) => {
      const unknownCalls = Math.max(0, Number(bucket?.unknown_price_calls) || 0);
      const calls = callCount(bucket) || fallbackCalls;
      if (!unknownCalls) return escapeHtml(fmtCost(bucket?.cost_usd));
      if (calls > unknownCalls) {
        return escapeHtml(t('tokens.cost_partial', { cost: fmtCost(bucket?.cost_usd), count: unknownCalls }));
      }
      return escapeHtml(t('tokens.cost_unknown'));
    };
    const totalUnknownCalls = Math.max(0, Number(totals.unknown_price_calls) || 0);
    const totalCost = costValue(totals, totalCalls);
    const totalCostNote = totalUnknownCalls > 0 && totalCalls > totalUnknownCalls
      ? `<div class="token-cost-note">${escapeHtml(t('tokens.unknown_price_hint'))}</div>`
      : '';
    const totalHtml = `
      <div style="display:flex;gap:var(--space-3);margin-bottom:var(--space-3);flex-wrap:wrap">
        <div class="token-card"><div class="token-card-value">${Number(totals.total_tokens || 0).toLocaleString()}</div><div class="token-card-label">${t('tokens.total_label')}</div></div>
        <div class="token-card"><div class="token-card-value">${Number(totals.prompt_tokens || 0).toLocaleString()}</div><div class="token-card-label">Prompt</div></div>
        <div class="token-card"><div class="token-card-value">${Number(totals.completion_tokens || 0).toLocaleString()}</div><div class="token-card-label">Completion</div></div>
        <div class="token-card"><div class="token-card-value">${totalCost}</div><div class="token-card-label">${t('tokens.total_cost')}</div>${totalCostNote}</div>
      </div>`;

    let modelHtml = `<h4 style="margin:var(--space-2) 0">${t('tokens.by_model')}</h4><table class="token-table"><tr><th>${t('tokens.col.model')}</th><th>${t('tokens.col.calls')}</th><th>Prompt</th><th>Completion</th><th>${t('tokens.col.total')}</th><th>${t('tokens.col.cost')}</th></tr>`;
    for (const [model, m] of Object.entries(data.by_model || {})) {
      modelHtml += `<tr><td>${escapeHtml(model)}</td><td>${callCount(m)}</td><td>${Number(m.prompt_tokens || 0).toLocaleString()}</td><td>${Number(m.completion_tokens || 0).toLocaleString()}</td><td>${Number(m.total_tokens || 0).toLocaleString()}</td><td>${costValue(m)}</td></tr>`;
    }
    modelHtml += '</table>';

    let taskHtml = `<h4 style="margin:var(--space-2) 0">${t('tokens.by_task')}</h4><table class="token-table"><tr><th>${t('tokens.col.task')}</th><th>${t('tokens.col.calls')}</th><th>Prompt</th><th>Completion</th><th>${t('tokens.col.total')}</th><th>${t('tokens.col.cost')}</th></tr>`;
    for (const [task, tk] of Object.entries(data.by_task || {})) {
      taskHtml += `<tr><td>${escapeHtml(task)}</td><td>${callCount(tk)}</td><td>${Number(tk.prompt_tokens || 0).toLocaleString()}</td><td>${Number(tk.completion_tokens || 0).toLocaleString()}</td><td>${Number(tk.total_tokens || 0).toLocaleString()}</td><td>${costValue(tk)}</td></tr>`;
    }
    taskHtml += '</table>';

    let historyHtml = `<h4 style="margin:var(--space-2) 0">${t('tokens.history')}</h4><table class="token-table"><tr><th>${t('tokens.col.time')}</th><th>${t('tokens.col.task')}</th><th>${t('tokens.col.provider')}</th><th>${t('tokens.col.model')}</th><th>${t('tokens.col.prompt_version')}</th><th>Prompt</th><th>Completion</th><th>${t('tokens.col.total')}</th><th>${t('tokens.col.cost')}</th></tr>`;
    for (const h of (data.history || []).slice().reverse().slice(0, 100)) {
      const historyCost = h.price_known === false
        ? escapeHtml(t('tokens.cost_unknown'))
        : escapeHtml(fmtCost(h.cost_usd));
      historyHtml += `<tr><td class="token-time">${escapeHtml(h.timestamp || '')}</td><td>${escapeHtml(h.task || '')}</td><td>${escapeHtml(h.provider || '')}</td><td>${escapeHtml(h.model || '')}</td><td>${escapeHtml(h.prompt_version || '')}</td><td>${Number(h.prompt_tokens || 0).toLocaleString()}</td><td>${Number(h.completion_tokens || 0).toLocaleString()}</td><td>${Number(h.total_tokens || 0).toLocaleString()}</td><td>${historyCost}</td></tr>`;
    }
    historyHtml += '</table>';

    pane.innerHTML = `<div style="padding:var(--space-2)">${totalHtml}${modelHtml}${taskHtml}${historyHtml}</div>`;
  } catch (e) {
    const message = t('tokens.load_failed', { detail: e.message || e });
    pane.innerHTML = `<p class="muted">${escapeHtml(message)}</p>`;
    setStatus(message, 'err');
  }
}
