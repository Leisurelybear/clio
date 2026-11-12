import { state } from './state.js';
import { $, escapeHtml } from './utils.js';
import { addToast } from './toast.js';
import { t } from './i18n.js';
import { insertSegment, nextExpandedAfterInsert } from './plan-edit.js';

let _insertAfterIndex = -1;
let _insertSelectedIndex = null;
let _insertModalBound = false;
let _onInsert = null;

function ensureInsertModalBound() {
  if (_insertModalBound) return;
  _insertModalBound = true;
  const modal = $('modal-plan-insert');
  if (!modal) return;
  modal.querySelector('.modal-backdrop')?.addEventListener('click', closeInsertModal);
  $('plan-insert-cancel')?.addEventListener('click', closeInsertModal);
  $('plan-insert-confirm')?.addEventListener('click', confirmInsertSegment);
  $('plan-insert-filter')?.addEventListener('input', (e) => {
    renderInsertVideoList(e.target.value || '');
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (modal.style.display !== 'flex') return;
    closeInsertModal();
  });
}

function closeInsertModal() {
  const modal = $('modal-plan-insert');
  if (modal) modal.style.display = 'none';
  _insertAfterIndex = -1;
  _insertSelectedIndex = null;
  _onInsert = null;
}

export function openPlanInsertModal(afterIndex, onInsert) {
  ensureInsertModalBound();
  const modal = $('modal-plan-insert');
  if (!modal) {
    addToast(t('plan.insert_unavailable'), 'error');
    return;
  }
  _insertAfterIndex = afterIndex;
  _insertSelectedIndex = null;
  _onInsert = typeof onInsert === 'function' ? onInsert : null;
  const filter = $('plan-insert-filter');
  if (filter) filter.value = '';
  const hint = $('plan-insert-hint');
  if (hint) {
    const n = (state.plan?.sequence?.length || 0);
    if (n === 0 || afterIndex < 0) {
      hint.textContent = t('plan.insert_hint_start');
    } else if (afterIndex >= n - 1) {
      hint.textContent = t('plan.insert_hint_end');
    } else {
      hint.textContent = t('plan.insert_hint_after', { index: afterIndex + 1 });
    }
  }
  renderInsertVideoList('');
  const confirmBtn = $('plan-insert-confirm');
  if (confirmBtn) confirmBtn.disabled = true;
  modal.style.display = 'flex';
  setTimeout(() => filter?.focus(), 0);
}

function renderInsertVideoList(query) {
  const list = $('plan-insert-list');
  if (!list) return;
  const q = String(query || '').trim().toLowerCase();
  const videos = (state.videos || []).filter((v) => v && v.index);
  const filtered = q
    ? videos.filter((v) => {
        const hay = `${v.index} ${v.title || ''} ${v.file || ''}`.toLowerCase();
        return hay.includes(q);
      })
    : videos;

  if (!filtered.length) {
    list.innerHTML = videos.length
      ? `<p class="muted">${t('plan.insert_no_match')}</p>`
      : `<p class="muted">${t('plan.insert_no_videos')}</p>`;
    return;
  }

  list.innerHTML = filtered.map((v) => {
    const idx = String(v.index);
    const title = v.title || v.file || t('plan.untitled');
    const selected = _insertSelectedIndex != null && String(_insertSelectedIndex) === idx;
    return `<button type="button" class="plan-insert-item${selected ? ' selected' : ''}" data-index="${escapeHtml(idx)}" title="${escapeHtml(v.file || '')}">
      <span class="plan-insert-item-idx">[${escapeHtml(idx)}]</span>
      <span class="plan-insert-item-title">${escapeHtml(title)}</span>
    </button>`;
  }).join('');

  list.querySelectorAll('.plan-insert-item').forEach((btn) => {
    btn.addEventListener('click', () => {
      _insertSelectedIndex = btn.dataset.index;
      list.querySelectorAll('.plan-insert-item').forEach((el) => {
        el.classList.toggle('selected', el.dataset.index === _insertSelectedIndex);
      });
      const confirmBtn = $('plan-insert-confirm');
      if (confirmBtn) confirmBtn.disabled = false;
    });
    btn.addEventListener('dblclick', () => {
      _insertSelectedIndex = btn.dataset.index;
      confirmInsertSegment();
    });
  });
}

function confirmInsertSegment() {
  const p = state.plan;
  if (!p) return;
  const index = _insertSelectedIndex != null ? String(_insertSelectedIndex).trim() : '';
  if (!index) {
    addToast(t('plan.pick_video_first'), 'warning');
    return;
  }
  const v = (state.videos || []).find((x) => String(x.index) === index);
  if (!v) {
    addToast(t('plan.video_not_found', { index }), 'warning');
    return;
  }
  const afterIndex = _insertAfterIndex;
  const title = v.title || v.file || '';
  const onInsert = _onInsert;
  closeInsertModal();
  const next = insertSegment(p.sequence, afterIndex, { index, title });
  const expandedIndex = nextExpandedAfterInsert(afterIndex);
  onInsert?.(next, expandedIndex);
}
