import { $, setStatus } from './utils.js';
import { api } from './api.js';
import { addToast } from './toast.js';
import { t } from './i18n.js';

let _oldPath = '';
let _inited = false;

export function openRelinkModal({ oldPath, displayName } = {}) {
  _ensureInit();
  const modal = $('modal-relink');
  if (!modal) return;
  _oldPath = oldPath || displayName || '';
  const oldEl = $('relink-old-path');
  const input = $('relink-new-path');
  const hint = $('relink-hint');
  if (oldEl) oldEl.textContent = _oldPath || t('sidebar.unknown_value');
  if (input) input.value = _oldPath || '';
  if (hint) {
    const name = displayName || (_oldPath.replace(/^.*[\\/]/, '') || t('sidebar.video_generic'));
    hint.textContent = t('sidebar.relink_hint', { name });
  }
  modal.style.display = 'flex';
  setTimeout(() => {
    input?.focus();
    input?.select();
  }, 0);
}

export function closeRelinkModal() {
  const modal = $('modal-relink');
  if (modal) modal.style.display = 'none';
  _oldPath = '';
}

async function _submitRelink() {
  const input = $('relink-new-path');
  const newPath = (input?.value || '').trim();
  if (!newPath) {
    setStatus(t('sidebar.need_new_path'), 'warn');
    addToast(t('sidebar.need_new_path'), 'warning', undefined, { persist: false });
    return;
  }
  if (newPath === _oldPath) {
    setStatus(t('sidebar.same_path'), 'warn');
    return;
  }
  const btn = $('relink-confirm');
  if (btn?.disabled) return;
  if (btn) {
    btn.disabled = true;
    btn.textContent = t('sidebar.linking');
  }
  try {
    const r = await api('PUT', '/api/videos/relink', {
      old_path: _oldPath,
      new_path: newPath,
    });
    if (r.ok) {
      const msg = t('sidebar.relinked', { path: newPath });
      setStatus(msg, 'ok');
      addToast(msg, 'success', undefined, { persist: false });
      closeRelinkModal();
      const { loadVideos } = await import('./sidebar-data.js');
      await loadVideos();
    } else {
      const msg = t('sidebar.relink_failed', { detail: r.error || t('common.unknown_error') });
      setStatus(msg, 'err');
      addToast(msg, 'error', 6000, { title: t('sidebar.relink_toast_title') });
    }
  } catch (e) {
    const msg = t('sidebar.relink_failed', { detail: e.message });
    setStatus(msg, 'err');
    addToast(msg, 'error', 6000, { title: t('sidebar.relink_toast_title') });
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = t('sidebar.confirm_relink');
    }
  }
}

function _ensureInit() {
  if (_inited) return;
  const modal = $('modal-relink');
  if (!modal) return;
  _inited = true;
  $('relink-cancel')?.addEventListener('click', closeRelinkModal);
  $('relink-confirm')?.addEventListener('click', () => { _submitRelink(); });
  const input = $('relink-new-path');
  if (input) {
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        _submitRelink();
      }
    });
  }
  // backdrop does not close (same as video manager)
  modal.querySelector('.modal-backdrop')?.addEventListener('click', (e) => {
    e.stopPropagation();
  });
}
