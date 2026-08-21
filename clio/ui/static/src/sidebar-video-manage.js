import { $, setStatus } from './utils.js';
import { api } from './api.js';
import { loadVideos } from './sidebar-data.js';
import { addToast } from './toast.js';

export async function openVideoManager() {
  const { pickFiles } = await import('./desktop-pick.js');
  const { state } = await import('./state.js');
  try {
    const paths = await pickFiles(state.currentProjectDir || '', 'video');
    if (!paths || paths.length === 0) return;
    await _addPaths(paths);
  } catch (e) {
    addToast(`文件浏览器不可用：${String(e.message || e)}`, 'error', 6000);
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
    btn.textContent = '保存中...';
  }
  try {
    const { mergeSelectedVideos } = await import('./video-selection.js');
    const current = await api('GET', '/api/videos/selected');
    const { merged, added, already } = mergeSelectedVideos(current.videos || [], list);
    const r = await api('PUT', '/api/videos/selected', { videos: merged });
    if (r && r.rejected_count) {
      const msg = `已添加，但有 ${r.rejected_count} 个路径被拒绝（扩展名无效或无法解析）`;
      setStatus(msg, 'warn');
      addToast(msg, 'warning', 6000, { persist: false });
    } else if (added === 0) {
      const msg = already
        ? `所选 ${already} 个均已在项目中`
        : '没有新视频需要添加';
      setStatus(msg, 'warn');
      addToast(msg, 'warning', undefined, { persist: false });
    } else {
      const msg = already
        ? `新增 ${added} 个视频（另 ${already} 个已存在）`
        : `已添加 ${added} 个视频`;
      setStatus(msg, 'ok');
      addToast(msg, 'success', undefined, { persist: false });
    }
    closeVideoManager();
    await loadVideos();
  } catch (e) {
    const msg = '添加视频失败: ' + e.message;
    setStatus(msg, 'err');
      addToast(msg, 'error', 6000, { title: '添加视频失败' });
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '添加';
    }
  }
}

async function _vmAddPasted() {
  const ta = $('vm-paste');
  const text = (ta?.value || '').trim();
  if (!text) {
    setStatus('请输入或粘贴视频绝对路径', 'warn');
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
        addToast('无法解析拖入路径，请手动输入绝对路径或使用系统对话框', 'warning');
        return;
      }
      const ok = absPaths.filter(p => {
        const base = p.replace(/^.*[\\/]/, '');
        const dot = base.lastIndexOf('.');
        const ext = dot > 0 ? base.slice(dot).toLowerCase() : '';
        return _VIDEO_EXTS_DND.has(ext);
      });
      if (ok.length === 0) {
        addToast('没有可添加的视频文件', 'warning');
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
