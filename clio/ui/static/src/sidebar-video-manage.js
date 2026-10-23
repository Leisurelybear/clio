import { $, setStatus } from './utils.js';
import { api } from './api.js';
import { loadVideos } from './sidebar-data.js';
import { addToast } from './toast.js';
import { t } from './i18n.js';

export async function openVideoManager() {
  const { pickFiles } = await import('./desktop-pick.js');
  const { state } = await import('./state.js');
  try {
    const paths = await pickFiles(state.currentProjectDir || '', 'video');
    if (!paths || paths.length === 0) return;
    await _addPaths(paths);
  } catch (e) {
    addToast(t('sidebar.file_browser_unavailable', { detail: String(e.message || e) }), 'error', 6000);
    _openServePasteModal();
  }
}

export function closeVideoManager() {
  const modal = $('modal-video-manage');
  if (!modal) return;
  modal.style.display = 'none';
  const ta = $('vm-paste');
  if (ta) ta.value = '';
}

function _openServePasteModal() {
  const modal = $('modal-video-manage');
  if (!modal) return;
  const ta = $('vm-paste');
  if (ta) ta.value = '';
  modal.style.display = 'flex';
}

async function _addPaths(paths) {
  const list = (paths || []).map(p => String(p).trim()).filter(Boolean);
  if (list.length === 0) return;
  const btn = $('vm-add');
  if (btn) {
    btn.disabled = true;
    btn.textContent = t('sidebar.saving');
  }
  try {
    const { mergeSelectedVideos } = await import('./video-selection.js');
    const current = await api('GET', '/api/videos/selected');
    const { merged, added, already } = mergeSelectedVideos(current.videos || [], list);
    const r = await api('PUT', '/api/videos/selected', { videos: merged, candidates: list });
    const addedCount = Number.isInteger(r?.added) ? r.added : added;
    const alreadyCount = Number.isInteger(r?.already) ? r.already : already;
    if (r && r.rejected_count) {
      const msg = t('sidebar.add_rejected', { count: r.rejected_count });
      setStatus(msg, 'warn');
      addToast(msg, 'warning', 6000, { persist: false });
    } else if (addedCount === 0) {
      const msg = alreadyCount
        ? t('sidebar.add_all_exist', { count: alreadyCount })
        : t('sidebar.add_nothing');
      setStatus(msg, 'warn');
      addToast(msg, 'warning', undefined, { persist: false });
    } else {
      const msg = alreadyCount
        ? t('sidebar.add_with_existing', { added: addedCount, existing: alreadyCount })
        : t('sidebar.add_count', { count: addedCount });
      setStatus(msg, 'ok');
      addToast(msg, 'success', undefined, { persist: false });
    }
    closeVideoManager();
    await loadVideos();
  } catch (e) {
    const msg = t('sidebar.add_failed', { detail: e.message });
    setStatus(msg, 'err');
      addToast(msg, 'error', 6000, { title: t('sidebar.add_failed_title') });
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = t('common.add');
    }
  }
}

async function _vmAddPasted() {
  const ta = $('vm-paste');
  const text = (ta?.value || '').trim();
  if (!text) {
    setStatus(t('sidebar.need_abs_path'), 'warn');
    return;
  }
  const paths = text.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  if (paths.length === 0) return;
  await _addPaths(paths);
}

const _VIDEO_EXTS_DND = new Set(['.mp4', '.mov', '.mkv', '.mts', '.m2ts', '.avi', '.wmv', '.flv', '.webm', '.3gp', '.mpg', '.mpeg']);

function _vmDropAbsolutePaths(dt) {
  const paths = [];
  const files = dt.files;
  if (files) {
    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      if (f && f.path && typeof f.path === 'string') {
        paths.push(f.path);
      }
    }
  }
  try {
    const uris = dt.getData('text/uri-list');
    if (uris) {
      for (const line of uris.split('\n')) {
        const uri = line.trim();
        if (uri.startsWith('file:///')) {
          let p = decodeURIComponent(uri.slice(8));
          if (p.match(/^[a-zA-Z]:/)) p = p[0].toUpperCase() + p.slice(1);
          paths.push(p);
        }
      }
    }
  } catch {}
  return paths;
}

function _vmInitDragDrop() {
  const zone = $('vm-dropzone');
  if (!zone) return;
  zone.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    zone.classList.add('drop-zone-active');
  });
  zone.addEventListener('dragleave', () => {
    zone.classList.remove('drop-zone-active');
  });
  zone.addEventListener('drop', async (e) => {
    e.preventDefault();
    zone.classList.remove('drop-zone-active');
    try {
      const dt = e.dataTransfer;
      if (!dt) return;
      const absPaths = _vmDropAbsolutePaths(dt);
      if (absPaths.length === 0) {
        addToast(t('sidebar.drag_unresolved'), 'warning');
        return;
      }
      const ok = absPaths.filter(p => {
        const base = p.replace(/^.*[\\/]/, '');
        const dot = base.lastIndexOf('.');
        const ext = dot > 0 ? base.slice(dot).toLowerCase() : '';
        return _VIDEO_EXTS_DND.has(ext);
      });
      if (ok.length === 0) {
        addToast(t('sidebar.no_addable'), 'warning');
        return;
      }
      await _addPaths(ok);
    } catch (err) {
      console.warn('video-manage drop error:', err);
    }
  });
}

function _vmInit() {
  const modal = $('modal-video-manage');
  if (!modal) return;
  // backdrop intentionally does NOT close — only Cancel button closes
  $('vm-cancel').onclick = closeVideoManager;
  $('vm-add').onclick = _vmAddPasted;
  _vmInitDragDrop();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _vmInit);
} else {
  _vmInit();
}
