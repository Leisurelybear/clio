/**
 * Empty-state HTML for per-video artifact tabs (texts / voiceover / transcript).
 * Callers bind click handlers via [data-empty-action].
 *
 * @param {string} kind
 * @param {{ hasVideo?: boolean, hasTexts?: boolean }} [opts]
 *   hasVideo defaults true (legacy). hasTexts defaults true for voiceover.
 */

import { t } from './i18n.js';

const _btnPrimary =
  'background:var(--accent);color:#fff;border:none;padding:7px 14px;border-radius:var(--radius-sm);cursor:pointer;font:inherit;font-size:var(--text-sm)';
const _btnSecondary =
  'background:var(--bg-surface-2);color:var(--text-primary);border:1px solid var(--border);padding:7px 14px;border-radius:var(--radius-sm);cursor:pointer;font:inherit;font-size:var(--text-sm)';

export function renderEmptyArtifactHtml(kind, opts = {}) {
  const hasVideo = opts.hasVideo !== false;
  if (!hasVideo) {
    return `
      <div class="empty-state empty-artifact">
        <h4>${t('empty.no_video.title')}</h4>
        <p class="muted">${t('empty.no_video.body')}</p>
      </div>`;
  }

  if (kind === 'texts') {
    return `
      <div class="empty-state empty-artifact">
        <h4>${t('empty.texts.title')}</h4>
        <p class="muted">${t('empty.texts.body')}</p>
        <p style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
          <button type="button" class="sidebar-btn empty-cta" data-empty-action="run" style="${_btnPrimary}">
            ${t('empty.cta.run')}
          </button>
          <button type="button" class="sidebar-btn empty-cta" data-empty-action="rerun-analyze" style="${_btnSecondary}">
            ${t('empty.cta.rerun_analyze')}
          </button>
        </p>
      </div>`;
  }

  if (kind === 'voiceover') {
    const hasTexts = opts.hasTexts !== false;
    if (!hasTexts) {
      return `
      <div class="empty-state empty-artifact">
        <h4>${t('empty.voiceover_needs_texts.title')}</h4>
        <p class="muted">${t('empty.voiceover_needs_texts.body')}</p>
        <p style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
          <button type="button" class="sidebar-btn empty-cta" data-empty-action="run" style="${_btnPrimary}">
            ${t('empty.cta.run')}
          </button>
          <button type="button" class="sidebar-btn empty-cta" data-empty-action="rerun-analyze" style="${_btnSecondary}">
            ${t('empty.cta.rerun_analyze')}
          </button>
        </p>
      </div>`;
    }
    return `
      <div class="empty-state empty-artifact">
        <h4>${t('empty.voiceover.title')}</h4>
        <p class="muted">${t('empty.voiceover.body')}</p>
        <p style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
          <button type="button" class="sidebar-btn empty-cta" data-empty-action="run" style="${_btnPrimary}">
            ${t('empty.cta.run')}
          </button>
          <button type="button" class="sidebar-btn empty-cta" data-empty-action="rerun-voiceover" style="${_btnSecondary}">
            ${t('empty.cta.rerun_voiceover')}
          </button>
        </p>
      </div>`;
  }

  if (kind === 'transcript') {
    return `
      <div class="empty-state empty-artifact">
        <h4>${t('empty.transcript.title')}</h4>
        <p class="muted">${t('empty.transcript.body')}</p>
        <p style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
          <button type="button" class="sidebar-btn empty-cta" data-empty-action="run" style="${_btnPrimary}">
            ${t('empty.cta.run')}
          </button>
          <button type="button" class="sidebar-btn empty-cta" data-empty-action="rerun-transcribe" style="${_btnSecondary}">
            ${t('empty.cta.rerun_transcribe')}
          </button>
        </p>
      </div>`;
  }

  return `<p class="muted">${t('empty.unknown_panel')}</p>`;
}

/**
 * Bind empty-state CTA buttons inside a pane.
 * @param {HTMLElement} pane
 * @param {{ onRun: () => void, onRerun: (task: string) => void }} handlers
 */
export function bindEmptyArtifactActions(pane, handlers) {
  if (!pane || !handlers) return;
  pane.querySelectorAll('[data-empty-action]').forEach((btn) => {
    btn.onclick = (e) => {
      e.preventDefault();
      const action = btn.dataset.emptyAction;
      if (action === 'run') handlers.onRun?.();
      else if (action === 'rerun-analyze') handlers.onRerun?.('analyze');
      else if (action === 'rerun-voiceover') handlers.onRerun?.('voiceover');
      else if (action === 'rerun-transcribe') handlers.onRerun?.('transcribe');
    };
  });
}
