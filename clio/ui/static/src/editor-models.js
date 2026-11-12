/**
 * Local Whisper model management in the global settings tab.
 */
import { api, icon } from './api.js';
import { confirmDialog } from './confirm.js';
import { t } from './i18n.js';
import { state } from './state.js';
import { fetchTask, subscribeTaskEvents } from './task-center.js';
import { transcriptionModelManagementNotice } from './transcription-status.js';
import { $, escapeHtml, setStatus } from './utils.js';

let _installPollTimer = null;
let _installTaskId = null;
let _installTaskUnsubscribe = null;

function _clearWhisperTaskSubscription() {
  if (_installTaskUnsubscribe) {
    _installTaskUnsubscribe();
    _installTaskUnsubscribe = null;
  }
  _installTaskId = null;
}

function _subscribeWhisperTask(taskId) {
  _clearWhisperTaskSubscription();
  if (!taskId) return;
  _installTaskId = taskId;
  _installTaskUnsubscribe = subscribeTaskEvents(payload => {
    const task = payload?.task;
    if (!task || task.id !== taskId || task.kind !== 'whisper_install') return;
    _applyWhisperTask(task, payload.event);
  });
  fetchTask(taskId).then(task => {
    if (task && task.id === taskId) _applyWhisperTask(task);
  }).catch(() => {});
}

function _applyWhisperTask(task, event = null) {
  const prog = $('model-dl-progress');
  const bar = $('model-dl-bar');
  const msg = $('model-dl-msg');
  const cancelBtn = $('btn-cancel-dl');
  const dlBtn = $('btn-model-download');
  const pct = Number.isFinite(task.progress_pct) ? task.progress_pct : 0;
  if (prog) prog.style.display = task.status === 'queued' || task.status === 'running' || task.status === 'cancelling' ? 'block' : 'none';
  if (bar) bar.style.width = `${Math.min(100, Math.max(0, pct))}%`;
  if (msg) msg.textContent = event?.message || task.message || task.phase || t('whisper_mgmt.downloading');
  if (cancelBtn) cancelBtn.style.display = task.status === 'queued' || task.status === 'running' || task.status === 'cancelling' ? '' : 'none';
  if (dlBtn && (task.status === 'queued' || task.status === 'running' || task.status === 'cancelling')) {
    dlBtn.disabled = true;
    dlBtn.textContent = task.status === 'cancelling' ? t('whisper_mgmt.cancelling') : t('whisper_mgmt.downloading');
  }
  if (!['succeeded', 'failed', 'cancelled', 'interrupted'].includes(task.status)) return;
  if (_installPollTimer) { clearInterval(_installPollTimer); _installPollTimer = null; }
  _clearWhisperTaskSubscription();
  if (dlBtn) { dlBtn.disabled = false; dlBtn.innerHTML = `${icon('download', 14)} ${t('whisper_mgmt.btn.download')}`; }
  if (task.status === 'succeeded') {
    setStatus(t('whisper_mgmt.download_done_status'), 'ok', { persist: false });
    loadModelManagement();
  } else if (task.status === 'cancelled') {
    setStatus(t('whisper_mgmt.download_cancelled'), 'warn');
  } else {
    setStatus(t('whisper_mgmt.download_failed', { detail: task.error_message || task.message || t('common.unknown_error') }), 'err');
  }
}

export function renderModelManagement() {
  const div = document.createElement('div');
  div.id = 'whisper-model-mgmt';
  div.style.cssText = 'padding:0;background:transparent;border:none;border-radius:0';
  div.innerHTML = `
    <div id="model-mgmt-content">
      <p class="muted">${t('common.loading')}</p>
    </div>
  `;
  return div;
}


export async function loadModelManagement() {
  const container = $('model-mgmt-content');
  if (!container) {
    setStatus(t('whisper_mgmt.dom_not_ready'), 'warn');
    return;
  }
  const timeoutId = setTimeout(() => {
    if (container && container.querySelector('.muted')) {
      container.innerHTML = `<p class="err">${t('whisper_mgmt.timeout_detail')}</p>`;
      setStatus(t('whisper_mgmt.timeout_status'), 'err');
    }
  }, 10000);
  try {
    const [data, check] = await Promise.all([
      api('GET', '/api/whisper/models'),
      api('GET', '/api/whisper/check').catch(() => ({ cublas: true })),
    ]);
    if (!data.ok) {
      clearTimeout(timeoutId);
      container.innerHTML = `<p class="err">${t('whisper_mgmt.list_failed')}</p>`;
      setStatus(data.error || t('whisper_mgmt.list_failed'), 'err');
      return;
    }

    const current = data.current_model || 'medium';
    const avail = data.available || [];
    const cached = data.cached || [];
    const cublasOk = check.cublas !== false;
    const frozen = check.frozen === true;

    const engineNotice = transcriptionModelManagementNotice(check);
    let html = engineNotice
      ? `<p class="hint" style="margin:0 0 10px">${escapeHtml(engineNotice)}</p>`
      : '';
    html += '<div style="display:flex;gap:12px;flex-wrap:wrap;align-items:end">';

    html += '<div style="flex:1;min-width:140px">';
    html += `<label style="font-size:var(--text-xs);color:var(--text-secondary)">${t('whisper_mgmt.current_model')}</label>`;
    html += '<select id="model-size-select" style="width:100%;margin-top:2px">';
    for (const m of avail) {
      const sel = m.name === current ? ' selected' : '';
      const cachedIcon = cached.some(c => c.name === m.name && c.valid) ? ' ✓' : '';
      html += `<option value="${escapeHtml(m.name)}"${sel}>${escapeHtml(m.label)}${cachedIcon}</option>`;
    }
    html += '</select></div>';

    html += '<div>';
    const alreadyCached = cached.some(c => c.name === current && c.valid);
    const needsReverify = alreadyCached && !cublasOk;
    let btnText;
    let btnDisabled;
    if (frozen) {
      btnText = t('whisper_mgmt.btn.frozen');
      btnDisabled = ' disabled';
    } else {
      btnText = needsReverify ? t('whisper_mgmt.btn.reverify') : alreadyCached ? t('whisper_mgmt.btn.cached') : t('whisper_mgmt.btn.download');
      btnDisabled = alreadyCached && !needsReverify ? ' disabled' : '';
    }
    html += `<button id="btn-model-download" class="btn-primary" style="font-size:var(--text-sm)"${btnDisabled}>${icon('download', 14)} ${btnText}</button>`;
    if (needsReverify) {
      html += `<p style="font-size:var(--text-xs);color:var(--warn,#b8860b);margin:4px 0 0">${t('whisper_mgmt.cublas_warn')}</p>`;
    }
    if (frozen) {
      html += `<p style="font-size:var(--text-xs);color:var(--warn,#b8860b);margin:4px 0 0">${t('whisper_mgmt.frozen_warn')}</p>`;
    }
    html += '</div>';

    html += '<div style="font-size:var(--text-xs);color:var(--text-secondary);white-space:nowrap">';
    html += t('whisper_mgmt.free_space', { size: escapeHtml(data.free_display || '?') });
    html += '</div>';

    html += '</div>';

    if (cached.length) {
      html += '<div style="margin-top:8px">';
      html += `<p style="font-size:var(--text-xs);color:var(--text-secondary);margin:0 0 4px">${t('whisper_mgmt.cached_models')}</p>`;
      for (const m of cached) {
        const validCls = m.valid ? 'ok' : 'err';
        html += '<div style="display:flex;gap:8px;align-items:center;padding:4px 0;font-size:var(--text-xs)">';
        html += `<span style="flex:1">${escapeHtml(m.name)} <span class="${validCls}">${m.valid ? '✓' : t('whisper_mgmt.incomplete')}</span> <span class="muted">${escapeHtml(m.size_display)}</span></span>`;
        html += `<button class="btn-delete-model" data-model="${escapeHtml(m.name)}" style="background:none;border:1px solid var(--err,#c44);color:var(--err,#c44);padding:2px 8px;border-radius:3px;cursor:pointer;font-size:var(--text-xs)">${t('common.delete')}</button>`;
        html += '</div>';
      }
      html += '</div>';
    } else {
      html += `<p class="muted" style="margin-top:8px;font-size:var(--text-xs)">${t('whisper_mgmt.no_cached')}</p>`;
    }

    html += '<div id="model-dl-progress" style="display:none;margin-top:8px">';
    html += '<div style="display:flex;justify-content:space-between;font-size:var(--text-xs);margin-bottom:4px">';
    html += '<span id="model-dl-msg"></span><span id="model-dl-speed"></span></div>';
    html += '<div style="background:#333;border-radius:3px;height:6px;overflow:hidden">';
    html += '<div id="model-dl-bar" style="background:var(--accent);border-radius:3px;height:100%;width:0%"></div></div>';
    html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-top:2px">';
    html += '<p id="model-dl-eta" class="muted" style="font-size:var(--text-xs);margin:0"></p>';
    html += `<button id="btn-cancel-dl" style="background:none;border:1px solid var(--err,#c44);color:var(--err,#c44);padding:2px 10px;border-radius:3px;cursor:pointer;font-size:var(--text-xs);display:none">${t('whisper_mgmt.cancel_download')}</button>`;
    html += '</div>';
    html += '</div>';

    container.innerHTML = html;

    const sel = $('model-size-select');
    if (sel) {
      sel.onchange = async () => {
        const newModel = sel.value;
        const dlBtn = $('btn-model-download');
        const isCached = cached.some(c => c.name === newModel && c.valid);
        const needsReverify = isCached && !cublasOk;
        if (dlBtn) {
          dlBtn.disabled = isCached && !needsReverify;
          dlBtn.innerHTML = isCached
            ? (needsReverify ? t('whisper_mgmt.btn.reverify') : t('whisper_mgmt.btn.cached'))
            : `${icon('download', 14)} ${t('whisper_mgmt.btn.download')}`;
        }
        try {
          const r = await api('PUT', '/api/whisper/model', { model_size: newModel });
          if (r.ok) {
            setStatus(t('whisper_mgmt.switched', { model: newModel }), 'ok');
          } else {
            setStatus(t('whisper_mgmt.switch_failed', { detail: r.error || '' }), 'err');
            sel.value = current;
          }
        } catch (e) {
          setStatus(t('whisper_mgmt.switch_failed', { detail: e.message }), 'err');
          sel.value = current;
        }
      };
    }

    const dlBtn = $('btn-model-download');
    if (dlBtn) {
      dlBtn.onclick = async () => {
        if (check.frozen === true) {
          setStatus(t('whisper_mgmt.frozen_status'), 'warn');
          return;
        }
        dlBtn.disabled = true;
        dlBtn.textContent = t('whisper_mgmt.starting');
        const prog = $('model-dl-progress');
        if (prog) prog.style.display = 'block';
        const cancelBtn = $('btn-cancel-dl');
        if (cancelBtn) cancelBtn.style.display = '';
        try {
          const r = await api('POST', '/api/whisper/install', {});
          if (!r.ok) throw new Error(r.error || t('whisper_mgmt.start_failed_bare'));
          dlBtn.textContent = t('whisper_mgmt.downloading');
          if (r.task_id || r.task?.id) {
            if (_installPollTimer) { clearInterval(_installPollTimer); _installPollTimer = null; }
            _subscribeWhisperTask(r.task_id || r.task.id);
            _applyWhisperTask(r.task || { id: r.task_id, kind: 'whisper_install', status: 'queued' });
          } else {
            _installPollTimer = setInterval(_pollModelDl, 1000);
            _pollModelDl();
          }
        } catch (e) {
          dlBtn.disabled = false;
          dlBtn.innerHTML = `${icon('download', 14)} ${t('whisper_mgmt.btn.download')}`;
          const progMsg = $('model-dl-msg');
          const message = t('whisper_mgmt.start_failed', { detail: e.message });
          if (progMsg) progMsg.textContent = message;
          void setStatus(message, 'err');
        }
      };
    }

    const cancelBtn = $('btn-cancel-dl');
    if (cancelBtn) {
      cancelBtn.onclick = async () => {
        const managedCancel = Boolean(_installTaskId);
        try {
          if (_installTaskId) await api('POST', `/api/tasks/${encodeURIComponent(_installTaskId)}/cancel`, {});
          else await api('POST', '/api/whisper/install/cancel', {});
        } catch { /* ignore */ }
        if (managedCancel) {
          cancelBtn.disabled = true;
          cancelBtn.textContent = t('whisper_mgmt.cancelling');
          return;
        }
        if (_installPollTimer) { clearInterval(_installPollTimer); _installPollTimer = null; }
        const prog = $('model-dl-progress');
        if (prog) prog.style.display = 'none';
        cancelBtn.style.display = 'none';
        const dlBtn = $('btn-model-download');
        if (dlBtn) { dlBtn.disabled = false; dlBtn.innerHTML = `${icon('download', 14)} ${t('whisper_mgmt.btn.download')}`; }
      };
    }

    container.querySelectorAll('.btn-delete-model').forEach(btn => {
      btn.onclick = async () => {
        const modelName = btn.dataset.model;
        if (!(await confirmDialog(t('whisper_mgmt.delete_confirm', { name: modelName }), { danger: true }))) return;
        try {
          const r = await api('POST', '/api/whisper/models/delete', { name: modelName });
          if (r.ok) {
            setStatus(t('whisper_mgmt.deleted', { name: modelName }), 'ok');
            loadModelManagement();
          } else {
            setStatus(t('whisper_mgmt.delete_failed', { detail: r.error || '' }), 'err');
          }
        } catch (e) {
          setStatus(t('whisper_mgmt.delete_failed', { detail: e.message }), 'err');
        }
      };
    });
    clearTimeout(timeoutId);
    try {
      const tasksResult = await api('GET', '/api/tasks?kind=whisper_install&visibility=all&limit=20');
      const managed = (tasksResult.tasks || []).find(task => ['queued', 'running', 'cancelling'].includes(task.status)
        && (!state.currentProjectDir || !task.project_id || task.project_id === state.currentProjectDir));
      if (managed) {
        const prog = $('model-dl-progress');
        if (prog) prog.style.display = 'block';
        const cancelBtn = $('btn-cancel-dl');
        if (cancelBtn) cancelBtn.style.display = '';
        const dlBtn = $('btn-model-download');
        if (dlBtn) { dlBtn.disabled = true; dlBtn.textContent = t('whisper_mgmt.downloading'); }
        if (_installPollTimer) { clearInterval(_installPollTimer); _installPollTimer = null; }
        _subscribeWhisperTask(managed.id);
        _applyWhisperTask(managed);
      }
    } catch {
      try {
        const st = await api('GET', '/api/whisper/install/status');
        if (st.running || st.status === 'downloading') {
          if (_installPollTimer) clearInterval(_installPollTimer);
          _installPollTimer = setInterval(_pollModelDl, 1000);
          _pollModelDl();
        }
      } catch { /* compatibility resume is not critical */ }
    }
  } catch (e) {
    clearTimeout(timeoutId);
    const message = t('tokens.load_failed', { detail: e.message });
    if (container) container.innerHTML = `<p class="err">${escapeHtml(message)}</p>`;
    setStatus(message, 'err');
  }
}


async function _pollModelDl() {
  try {
    const s = await api('GET', '/api/whisper/install/status');
    const bar = $('model-dl-bar');
    const msg = $('model-dl-msg');
    const speed = $('model-dl-speed');
    const eta = $('model-dl-eta');
    const dlBtn = $('btn-model-download');
    const cancelBtn = $('btn-cancel-dl');
    if (!s.running && s.status === 'idle') {
      if (_installPollTimer) { clearInterval(_installPollTimer); _installPollTimer = null; }
      if (cancelBtn) cancelBtn.style.display = 'none';
      return;
    }
    if (s.status === 'downloading') {
      if (cancelBtn) cancelBtn.style.display = '';
      if (bar) bar.style.width = (s.progress_pct || 0) + '%';
      if (msg) msg.textContent = s.message || t('whisper_mgmt.downloading');
      if (speed && s.speed) speed.textContent = s.speed;
      if (eta) {
        if (s.eta_sec != null) {
          const m = Math.floor(s.eta_sec / 60);
          const sec = s.eta_sec % 60;
          eta.textContent = t('whisper_mgmt.eta', { min: m, sec });
        } else { eta.textContent = ''; }
      }
    } else if (s.status === 'done') {
      if (_installPollTimer) { clearInterval(_installPollTimer); _installPollTimer = null; }
      if (cancelBtn) cancelBtn.style.display = 'none';
      if (bar) bar.style.width = '100%';
      if (msg) msg.textContent = t('whisper_mgmt.done_msg');
      void setStatus(t('whisper_mgmt.download_done_status'), 'ok');
      if (eta) eta.textContent = '';
      if (dlBtn) { dlBtn.disabled = false; dlBtn.innerHTML = `${icon('download', 14)} ${t('whisper_mgmt.btn.download')}`; }
      loadModelManagement();
    } else if (s.status === 'error') {
      if (_installPollTimer) { clearInterval(_installPollTimer); _installPollTimer = null; }
      if (cancelBtn) cancelBtn.style.display = 'none';
      if (dlBtn) { dlBtn.disabled = false; dlBtn.innerHTML = `${icon('download', 14)} ${t('whisper_mgmt.retry_download')}`; }
      const message = s.message || t('whisper_mgmt.failed_bare');
      if (msg) msg.textContent = message;
      void setStatus(message, 'err');
    }
  } catch { /* ignore */ }
}
