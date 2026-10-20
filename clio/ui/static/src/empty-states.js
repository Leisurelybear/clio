/**
 * Empty-state HTML for per-video artifact tabs (texts / voiceover / transcript).
 * Callers bind click handlers via [data-empty-action].
 *
 * @param {string} kind
 * @param {{ hasVideo?: boolean, hasTexts?: boolean }} [opts]
 *   hasVideo defaults true (legacy). hasTexts defaults true for voiceover.
 */

const _btnPrimary =
  'background:var(--accent);color:#fff;border:none;padding:7px 14px;border-radius:var(--radius-sm);cursor:pointer;font:inherit;font-size:var(--text-sm)';
const _btnSecondary =
  'background:var(--bg-surface-2);color:var(--text-primary);border:1px solid var(--border);padding:7px 14px;border-radius:var(--radius-sm);cursor:pointer;font:inherit;font-size:var(--text-sm)';

export function renderEmptyArtifactHtml(kind, opts = {}) {
  const hasVideo = opts.hasVideo !== false;
  if (!hasVideo) {
    return `
      <div class="empty-state empty-artifact">
        <h4>请先选择左侧视频</h4>
        <p class="muted">选中一条视频后，可在此查看并编辑分析、口播或转录结果。</p>
      </div>`;
  }

  if (kind === 'texts') {
    return `
      <div class="empty-state empty-artifact">
        <h4>没有分析结果</h4>
        <p class="muted">当前视频还没有 texts JSON。可先跑流水线，或单独重跑 AI 分析。</p>
        <p style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
          <button type="button" class="sidebar-btn empty-cta" data-empty-action="run" style="${_btnPrimary}">
            去运行流水线
          </button>
          <button type="button" class="sidebar-btn empty-cta" data-empty-action="rerun-analyze" style="${_btnSecondary}">
            重跑 AI 分析
          </button>
        </p>
      </div>`;
  }

  if (kind === 'voiceover') {
    const hasTexts = opts.hasTexts !== false;
    if (!hasTexts) {
      return `
      <div class="empty-state empty-artifact">
        <h4>需要先完成 AI 分析</h4>
        <p class="muted">口播依赖分析结果。请先生成 texts，再重跑口播。</p>
        <p style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
          <button type="button" class="sidebar-btn empty-cta" data-empty-action="run" style="${_btnPrimary}">
            去运行流水线
          </button>
          <button type="button" class="sidebar-btn empty-cta" data-empty-action="rerun-analyze" style="${_btnSecondary}">
            重跑 AI 分析
          </button>
        </p>
      </div>`;
    }
    return `
      <div class="empty-state empty-artifact">
        <h4>没有口播文案</h4>
        <p class="muted">当前视频还没有 voiceover JSON。需先有分析结果，再生成口播。</p>
        <p style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
          <button type="button" class="sidebar-btn empty-cta" data-empty-action="run" style="${_btnPrimary}">
            去运行流水线
          </button>
          <button type="button" class="sidebar-btn empty-cta" data-empty-action="rerun-voiceover" style="${_btnSecondary}">
            重跑口播
          </button>
        </p>
      </div>`;
  }

  if (kind === 'transcript') {
    return `
      <div class="empty-state empty-artifact">
        <h4>没有转录</h4>
        <p class="muted">当前视频还没有语音转录结果。可在运行面板勾选转录，或对当前视频单独转写。</p>
        <p style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
          <button type="button" class="sidebar-btn empty-cta" data-empty-action="run" style="${_btnPrimary}">
            去运行流水线
          </button>
          <button type="button" class="sidebar-btn empty-cta" data-empty-action="rerun-transcribe" style="${_btnSecondary}">
            重跑转录
          </button>
        </p>
      </div>`;
  }

  return '<p class="muted">未知面板</p>';
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
