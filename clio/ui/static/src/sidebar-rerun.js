import { state } from './state.js';
import { $, escapeHtml, setStatus, updateEntityUI } from './utils.js';
import { api } from './api.js';
import { addToast } from './toast.js';
import { loadVideos, renderVideoList } from './sidebar-data.js';
import { subscribeTaskEvents, fetchTask } from './task-center.js';

let _rerunPollTimer = null;
let _rerunPollStart = 0;
let _rerunTaskUnsubscribe = null;
// Long AI analyze can exceed 2 min; only fail after long idle wall clock
const RERUN_POLL_TIMEOUT = 30 * 60_000;

/** Menu + empty CTA send task "analyze"; legacy alias "texts". */
export function shouldReloadTextsAfterRerun(task) {
  return task === 'analyze' || task === 'texts' || task === 'all';
}

export function shouldReloadVoiceoverAfterRerun(task) {
  return task === 'voiceover' || task === 'all';
}

export function showRerunProgress(task, file, taskId = null) {
  const overlay = $('rerun-overlay');
  if (!overlay) return;
  overlay.classList.add('active');
  overlay.style.display = 'block';
  overlay.dataset.active = 'true';

  overlay.querySelector('.rerun-title').textContent = `重跑 ${task}`;
  overlay.querySelector('.rerun-file').textContent = file;
  overlay.querySelector('.rerun-status').textContent = '启动中...';
  overlay.querySelector('.rerun-progress-fill').style.width = '0%';
  overlay.querySelector('.rerun-logs').innerHTML = '<div class="rerun-log-line">连接中...</div>';

  if (_rerunPollTimer) clearInterval(_rerunPollTimer);
  if (_rerunTaskUnsubscribe) { _rerunTaskUnsubscribe(); _rerunTaskUnsubscribe = null; }
  _rerunPollStart = Date.now();
  if (taskId) {
    _rerunTaskUnsubscribe = subscribeTaskEvents(payload => {
      const current = payload?.task;
      if (!current || current.id !== taskId || current.kind !== 'rerun') return;
      _applyTaskEvent(task, file, current, payload.event);
    });
    fetchTask(taskId).then(current => {
      if (current && current.id === taskId) _applyTaskEvent(task, file, current);
    }).catch(() => {});
  } else {
    _rerunPollTimer = setInterval(() => pollRerunStatus(task, file), 1500);
    pollRerunStatus(task, file);
  }
}

export function hideRerunProgress() {
  const overlay = $('rerun-overlay');
  if (!overlay) return;
  overlay.classList.remove('active');
  overlay.style.display = 'none';
  if (_rerunPollTimer) {
    clearInterval(_rerunPollTimer);
    _rerunPollTimer = null;
  }
  if (_rerunTaskUnsubscribe) {
    _rerunTaskUnsubscribe();
    _rerunTaskUnsubscribe = null;
  }
}

function _applyTaskEvent(task, file, current, event) {
  const overlay = $('rerun-overlay');
  if (!overlay || overlay.dataset.active !== 'true') return;
  const fill = overlay.querySelector('.rerun-progress-fill');
  const statusEl = overlay.querySelector('.rerun-status');
  const logsEl = overlay.querySelector('.rerun-logs');
  if (fill && Number.isFinite(current.progress_pct)) fill.style.width = `${Math.min(100, Math.max(0, current.progress_pct))}%`;
  if (statusEl) statusEl.textContent = event?.message || current.message || current.phase || '运行中...';
  if (logsEl && event?.type === 'log' && event.message) {
    logsEl.insertAdjacentHTML('beforeend', `<div class="rerun-log-line">${escapeHtml(event.message)}</div>`);
    logsEl.scrollTop = logsEl.scrollHeight;
  }
  if (current.status === 'succeeded') {
    _rerunTerminal(task, file, '✓ 完成', '重跑完成', 'success', 'success');
  } else if (current.status === 'cancelled') {
    _rerunTerminal(task, file, '⏹ 已取消', '重跑已取消', 'warning', 'warning');
  } else if (current.status === 'failed' || current.status === 'interrupted') {
    _rerunTerminal(task, file, '✗ 出错', '重跑出错', 'error', 'error');
  }
}

function _rerunTerminal(task, file, label, message, toastKind, statusKind) {
  const overlay = $('rerun-overlay');
  if (!overlay || overlay.dataset.active !== 'true') return;
  overlay.dataset.active = 'false';
  if (_rerunPollTimer) { clearInterval(_rerunPollTimer); _rerunPollTimer = null; }
  if (_rerunTaskUnsubscribe) { _rerunTaskUnsubscribe(); _rerunTaskUnsubscribe = null; }
  const statusEl = overlay.querySelector('.rerun-status');
  if (statusEl) statusEl.innerHTML = `<span class="${statusKind === 'success' ? 'ok' : statusKind === 'warning' ? 'warn' : 'err'}">${escapeHtml(label)}</span>`;
  setStatus(message, statusKind === 'success' ? 'ok' : statusKind === 'warning' ? 'warn' : 'err', { persist: false });
  addToast(message, toastKind, statusKind === 'error' ? 6000 : undefined, { persist: false });
  if (statusKind === 'success') {
    setTimeout(() => { hideRerunProgress(); refreshAfterRerun(task, file); }, 2000);
  } else {
    setTimeout(hideRerunProgress, statusKind === 'error' ? 8000 : 4000);
  }
}

function _rerunPollError(statusEl, label, msg) {
  const overlay = $('rerun-overlay');
  if (!overlay) return;
  overlay.dataset.active = 'false';
  if (_rerunPollTimer) { clearInterval(_rerunPollTimer); _rerunPollTimer = null; }
  if (statusEl) statusEl.innerHTML = `<span class="err">✗ ${escapeHtml(label)}</span>`;
  setStatus(msg, 'err');
  addToast(msg, 'error', 6000);
  setTimeout(hideRerunProgress, 8000);
}

async function pollRerunStatus(task, file) {
  const overlay = $('rerun-overlay');
  if (!overlay || overlay.dataset.active !== 'true') return;

  try {
    const s = await api('GET', '/api/run/status');
    const fill = overlay.querySelector('.rerun-progress-fill');
    const statusEl = overlay.querySelector('.rerun-status');
    const logsEl = overlay.querySelector('.rerun-logs');

    if (Date.now() - _rerunPollStart > RERUN_POLL_TIMEOUT) {
      return _rerunPollError(statusEl, '超时', '重跑超时，请检查后端状态');
    }

    if (s.status === 'idle' || s.status === 'unknown') {
      if (Date.now() - _rerunPollStart > 10_000) {
        return _rerunPollError(statusEl, '未启动', '重跑任务未启动');
      }
      return;
    }

    if (fill && s.total > 0) {
      const pct = Math.round(s.current / s.total * 100);
      fill.style.width = Math.min(pct, 100) + '%';
    }

    if (statusEl) {
      statusEl.textContent = s.message || s.phase || '运行中...';
    }

    if (logsEl && s.logs && s.logs.length) {
      logsEl.innerHTML = s.logs.map(line =>
        `<div class="rerun-log-line">${escapeHtml(line)}</div>`
      ).join('');
      logsEl.scrollTop = logsEl.scrollHeight;
    }

    if (s.status === 'done') {
      overlay.dataset.active = 'false';
      if (_rerunPollTimer) {
        clearInterval(_rerunPollTimer);
        _rerunPollTimer = null;
      }
      if (statusEl) statusEl.innerHTML = '<span class="ok">✓ 完成</span>';
      setStatus('重跑完成', 'ok', { persist: false });
      addToast('重跑完成', 'success', undefined, { persist: false });
      setTimeout(() => {
        hideRerunProgress();
        refreshAfterRerun(task, file);
      }, 2000);
    } else if (s.status === 'cancelled') {
      overlay.dataset.active = 'false';
      if (_rerunPollTimer) {
        clearInterval(_rerunPollTimer);
        _rerunPollTimer = null;
      }
      if (statusEl) statusEl.innerHTML = '<span class="warn">⏹ 已取消</span>';
      setStatus('重跑已取消', 'warn', { persist: false });
      addToast('重跑已取消', 'warning', undefined, { persist: false });
      setTimeout(hideRerunProgress, 4000);
    } else if (s.status === 'error') {
      overlay.dataset.active = 'false';
      if (_rerunPollTimer) {
        clearInterval(_rerunPollTimer);
        _rerunPollTimer = null;
      }
      if (statusEl) statusEl.innerHTML = '<span class="err">✗ 出错</span>';
      setStatus('重跑出错', 'err', { persist: false });
      addToast('重跑出错', 'error', 6000, { persist: false });
      setTimeout(() => {
        hideRerunProgress();
      }, 8000);
    }
  } catch (e) {
    // poll error, ignore
  }
}

async function refreshAfterRerun(task, file) {
  await loadVideos();

  if (file && state.currentVideo === file) {
    const v = state.videos.find(x => x.file === file);
    if (!v) return;
    try {
      if (shouldReloadTextsAfterRerun(task) && v.text_json) {
        state.texts = await api('GET', `/api/texts?file=${encodeURIComponent(v.text_json)}`);
      }
      if (shouldReloadVoiceoverAfterRerun(task) && v.script_json) {
        state.voiceover = await api('GET', `/api/voiceover?file=${encodeURIComponent(v.script_json)}`);
      }
      import('./editor.js').then(mod => mod.renderActiveTab());
      updateEntityUI();
    } catch (e) {
      // content may not exist yet, ignore
    }
  }

  renderVideoList();
}
