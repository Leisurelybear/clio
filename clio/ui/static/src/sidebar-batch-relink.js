import { $, escapeHtml, setStatus } from './utils.js';
import { api } from './api.js';
import { addToast } from './toast.js';
import { matchBatchRelink } from './offline-media.js';
import { state } from './state.js';

let _pendingMatches = [];
let _inited = false;

function _pathValue() {
  return ($('br-path-input')?.value || '').trim();
}

export async function openBatchRelinkModal() {
  _ensureInit();
  const modal = $('modal-batch-relink');
  if (!modal) return;
  _pendingMatches = [];
  const result = $('br-result');
  if (result) result.innerHTML = '';
  const apply = $('br-apply');
  if (apply) apply.disabled = true;
  modal.style.display = 'flex';
  _updateScanEnabled();
  const { pickFolder } = await import('./desktop-pick.js');
  try {
    const dir = await pickFolder(state.currentProjectDir || '');
    if (!dir) return;
    const input = $('br-path-input');
    if (input) input.value = dir;
    _updateScanEnabled();
    await _scanAndMatch();
  } catch (e) {
    addToast(String(e.message || e), 'error', 6000);
  }
}

export function closeBatchRelinkModal() {
  const modal = $('modal-batch-relink');
  if (modal) modal.style.display = 'none';
  _pendingMatches = [];
}

function _updateScanEnabled() {
  const scanBtn = $('br-scan');
  if (scanBtn) scanBtn.disabled = !_pathValue();
}

async function _scanAndMatch() {
  const path = _pathValue();
  const result = $('br-result');
  const apply = $('br-apply');
  const scanBtn = $('br-scan');
  if (!result || !path) return;
  if (scanBtn) {
    scanBtn.disabled = true;
    scanBtn.textContent = '扫描中...';
  }
  result.innerHTML = '<p class="muted">正在扫描视频并匹配…</p>';
  try {
    const videosRes = await api('GET', `/api/fs/videos?path=${encodeURIComponent(path)}`);
    const candidates = (videosRes.files || []).map(f => ({
      path: f.path,
      name: f.name || f.path,
    }));
    const offline = (state.videos || [])
      .filter(v => v.missing)
      .map(v => ({
        file: v.file,
        abs_path: v.abs_path || v.match?.abs_path || null,
      }));
    const match = matchBatchRelink(offline, candidates);
    _pendingMatches = match.matched;
    if (apply) apply.disabled = match.matched.length === 0;

    let html = `<p><strong>匹配 ${match.matched.length}</strong> · 未匹配 ${match.unmatched.length}`;
    if (match.ambiguous.length) html += ` · 歧义 ${match.ambiguous.length}`;
    html += ` · 候选 ${candidates.length}</p>`;
    if (match.matched.length) {
      html += '<ul class="br-match-list">' + match.matched.map(m =>
        `<li><code>${escapeHtml(m.file)}</code><br><span class="muted">${escapeHtml(m.old_path)} → ${escapeHtml(m.new_path)}</span></li>`
      ).join('') + '</ul>';
    }
    if (match.ambiguous.length) {
      html += '<p class="warn">以下文件名在目录中出现多次，已跳过：' +
        match.ambiguous.map(a => escapeHtml(a.basename)).join(', ') + '</p>';
    }
    if (!match.matched.length) {
      html += '<p class="muted">没有可应用的匹配。确认目录中包含与离线视频同名的文件。</p>';
    }
    result.innerHTML = html;
  } catch (e) {
    const message = '扫描失败: ' + e.message;
    result.innerHTML = `<p class="err">${escapeHtml(message)}</p>`;
    setStatus(message, 'err');
    _pendingMatches = [];
    if (apply) apply.disabled = true;
  } finally {
    if (scanBtn) {
      scanBtn.textContent = '扫描此目录并匹配';
      _updateScanEnabled();
    }
  }
}

async function _applyMatches() {
  if (!_pendingMatches.length) return;
  const apply = $('br-apply');
  if (apply?.disabled) return;
  if (apply) {
    apply.disabled = true;
    apply.textContent = '应用中...';
  }
  let ok = 0;
  let fail = 0;
  for (const m of _pendingMatches) {
    try {
      const r = await api('PUT', '/api/videos/relink', {
        old_path: m.old_path,
        new_path: m.new_path,
      });
      if (r.ok) ok++;
      else fail++;
    } catch {
      fail++;
    }
  }
  const msg = `批量关联完成：成功 ${ok}` + (fail ? `，失败 ${fail}` : '');
  setStatus(msg, fail ? 'warn' : 'ok');
  addToast(msg, fail ? 'warning' : 'success', 6000, { persist: false });
  if (apply) {
    apply.textContent = '应用匹配';
    apply.disabled = true;
  }
  closeBatchRelinkModal();
  const { loadVideos } = await import('./sidebar-data.js');
  await loadVideos();
}

function _ensureInit() {
  if (_inited) return;
  if (!$('modal-batch-relink')) return;
  _inited = true;
  $('br-cancel')?.addEventListener('click', closeBatchRelinkModal);
  $('br-scan')?.addEventListener('click', () => { _scanAndMatch(); });
  $('br-apply')?.addEventListener('click', () => { _applyMatches(); });
  const input = $('br-path-input');
  if (input) {
    input.addEventListener('input', _updateScanEnabled);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        if (!_pathValue()) return;
        _scanAndMatch();
      }
    });
  }
  $('modal-batch-relink')?.querySelector('.modal-backdrop')?.addEventListener('click', (e) => e.stopPropagation());
}
