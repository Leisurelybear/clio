import { $, setStatus } from './utils.js';
import { api } from './api.js';
import { addToast } from './toast.js';

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
  if (oldEl) oldEl.textContent = _oldPath || '(未知)';
  if (input) input.value = _oldPath || '';
  if (hint) {
    const name = displayName || (_oldPath.replace(/^.*[\\/]/, '') || '视频');
    hint.textContent = `「${name}」当前离线。可直接粘贴/输入新路径，或点「浏览」选择文件。`;
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
    setStatus('请输入或选择新路径', 'warn');
    addToast('请输入或选择新路径', 'warning', undefined, { persist: false });
    return;
  }
  if (newPath === _oldPath) {
    setStatus('新路径与原路径相同，无需关联', 'warn');
    return;
  }
  const btn = $('relink-confirm');
  if (btn?.disabled) return;
  if (btn) {
    btn.disabled = true;
    btn.textContent = '关联中...';
  }
  try {
    const r = await api('PUT', '/api/videos/relink', {
      old_path: _oldPath,
      new_path: newPath,
    });
    if (r.ok) {
      const msg = `已重新关联: ${newPath}`;
      setStatus(msg, 'ok');
      addToast(msg, 'success', undefined, { persist: false });
      closeRelinkModal();
      const { loadVideos } = await import('./sidebar-data.js');
      await loadVideos();
    } else {
      const msg = '重新关联失败: ' + (r.error || '未知错误');
      setStatus(msg, 'err');
      addToast(msg, 'error', 6000, { persist: false });
    }
  } catch (e) {
    const msg = '重新关联失败: ' + e.message;
    setStatus(msg, 'err');
    addToast(msg, 'error', 6000, { persist: false });
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '确认关联';
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
