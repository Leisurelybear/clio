import { api } from './api.js';
import { subscribeTask } from './task-center.js';
import { addToast } from './toast.js';
import { hasKey, t } from './i18n.js';

export const FFMPEG_INSTALL_STATUS_URL = '/api/deps/ffmpeg/install/status';
export const FFMPEG_INSTALL_URL = '/api/deps/ffmpeg/install';

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'interrupted']);

export function detectPlatform(userAgent = globalThis.navigator?.userAgent || '', platform = globalThis.navigator?.platform || '') {
  const value = `${userAgent} ${platform}`.toLowerCase();
  if (value.includes('win')) return 'win32';
  if (value.includes('mac') || value.includes('darwin')) return 'darwin';
  if (value.includes('linux') || value.includes('x11')) return 'linux';
  return 'unknown';
}

export function manualInstallGuidance(platform) {
  if (platform === 'darwin') {
    return {
      steps: [
        t('ffmpeg.guide.darwin.step1'),
        t('ffmpeg.guide.darwin.step2'),
        t('ffmpeg.guide.darwin.step3'),
      ],
      downloadUrl: 'https://ffmpeg.org/download.html#build-mac',
      downloadLabel: t('ffmpeg.guide.download_manual'),
    };
  }
  if (platform === 'linux') {
    return {
      steps: [
        t('ffmpeg.guide.linux.step1'),
        t('ffmpeg.guide.linux.step2'),
        t('ffmpeg.guide.linux.step3'),
      ],
      downloadUrl: 'https://ffmpeg.org/download.html#build-linux',
      downloadLabel: t('ffmpeg.guide.download_manual'),
    };
  }
  if (platform === 'win32') {
    return {
      steps: [
        t('ffmpeg.guide.win32.step1'),
        t('ffmpeg.guide.win32.step2'),
        t('ffmpeg.guide.win32.step3'),
      ],
      downloadUrl: 'https://www.gyan.dev/ffmpeg/builds/',
      downloadLabel: t('ffmpeg.guide.download'),
    };
  }
  return {
    steps: [
      t('ffmpeg.guide.unknown.step1'),
      t('ffmpeg.guide.unknown.step2'),
      t('ffmpeg.guide.unknown.step3'),
    ],
    downloadUrl: 'https://ffmpeg.org/download.html',
    downloadLabel: t('ffmpeg.guide.download'),
  };
}

export function taskProgress(task) {
  const value = Number(task?.progress_pct);
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

export function taskStatusText(task) {
  const status = task?.status || 'queued';
  const key = `ffmpeg.status.${status}`;
  return hasKey(key) ? t(key) : status;
}

function appendGuidance(container, platform) {
  const guidance = manualInstallGuidance(platform);
  const list = document.createElement('ol');
  list.className = 'ffmpeg-install-steps';
  for (const step of guidance.steps) {
    const item = document.createElement('li');
    item.textContent = step;
    item.style.margin = '6px 0';
    list.appendChild(item);
  }
  container.replaceChildren(list);
  const link = document.createElement('a');
  link.href = guidance.downloadUrl;
  link.target = '_blank';
  link.rel = 'noopener';
  link.className = 'btn-secondary';
  link.textContent = guidance.downloadLabel;
  link.style.textDecoration = 'none';
  container.appendChild(link);
}

export function openFfmpegInstallDialog(options = {}) {
  const apiFn = options.apiFn || api;
  const subscribe = options.subscribe || subscribeTask;
  const toast = options.toast || addToast;
  const refreshDeps = options.refreshDeps || (async () => ({ ok: false }));
  let platform = options.platform || detectPlatform();
  let taskId = null;
  let stopTask = null;
  let subscriptionToken = 0;
  let terminalTaskId = null;
  let closed = false;

  const backdrop = document.createElement('div');
  backdrop.className = 'modal';
  backdrop.style.display = 'flex';
  backdrop.innerHTML = `
    <div class="modal-backdrop"></div>
    <div class="modal-dialog" style="max-width:600px">
      <h3 style="margin:0 0 var(--space-4)">${t('ffmpeg.dialog.title')}</h3>
      <p class="muted" style="margin:0 0 12px">${t('ffmpeg.dialog.intro')}</p>
      <section id="ffi-auto">
        <p id="ffi-auto-note" class="muted" style="font-size:var(--text-xs);margin:0 0 8px"></p>
        <div class="modal-actions" style="justify-content:flex-start;margin:0 0 10px">
          <button id="ffi-start" class="btn-primary">${t('ffmpeg.install.start')}</button>
          <button id="ffi-cancel" class="btn-secondary" style="display:none">${t('ffmpeg.install.cancel')}</button>
        </div>
        <div id="ffi-progress-box" style="display:none;margin:0 0 12px">
          <div style="display:flex;align-items:center;gap:10px">
            <progress id="ffi-progress" max="100" value="0" style="flex:1"></progress>
            <span id="ffi-percent" class="muted" style="font-size:var(--text-xs);min-width:36px"></span>
          </div>
          <p id="ffi-status" class="muted" role="status" style="font-size:var(--text-xs);margin:6px 0 0"></p>
          <p id="ffi-detail" class="muted" style="font-size:var(--text-xs);margin:4px 0 0"></p>
        </div>
      </section>
      <details id="ffi-manual" style="margin:0 0 16px">
        <summary style="cursor:pointer;font-size:var(--text-sm)">${t('ffmpeg.dialog.manual_summary')}</summary>
        <div id="ffi-manual-body" style="margin-top:10px;font-size:var(--text-sm)"></div>
      </details>
      <p class="muted" style="font-size:var(--text-xs);margin:0">${t('ffmpeg.dialog.notice')}</p>
      <div class="modal-actions" style="margin-top:16px">
        <button id="ffi-reprobe" class="btn-secondary">${t('ffmpeg.dialog.reprobe')}</button>
        <button id="ffi-close" class="btn-secondary">${t('common.close')}</button>
      </div>
    </div>
  `;
  document.body.appendChild(backdrop);
  const $id = (id) => backdrop.querySelector(`#${id}`);
  const startButton = $id('ffi-start');
  const cancelButton = $id('ffi-cancel');
  const progressBox = $id('ffi-progress-box');
  const progress = $id('ffi-progress');
  const percent = $id('ffi-percent');
  const statusText = $id('ffi-status');
  const detail = $id('ffi-detail');
  const automaticSection = $id('ffi-auto');
  const autoNote = $id('ffi-auto-note');

  // The start button is the primary action: it installs, and once an install has
  // succeeded it re-probes instead. Track that in a flag rather than comparing the
  // rendered label, because the label is localized and the comparison would
  // silently pick the wrong branch after a language switch.
  let startMode = 'install';

  const setStartLabel = (mode, label) => {
    startMode = mode;
    startButton.textContent = label;
  };

  const setAutomatic = (automatic) => {
    startButton.disabled = !automatic;
    startButton.title = automatic ? '' : t('ffmpeg.title.manual_only');
    autoNote.textContent = automatic
      ? t('ffmpeg.note.automatic')
      : t('ffmpeg.note.manual_only');
  };

  const setStatus = (message, type = '') => {
    statusText.textContent = message;
    statusText.style.color = type === 'error' ? 'var(--err, #c44)' : type === 'ok' ? 'var(--ok, #2a7)' : '';
  };

  const updateTask = (task) => {
    if (!task || closed) return;
    const incomingId = task.id || taskId;
    if (incomingId && terminalTaskId === incomingId) return;
    taskId = incomingId || taskId;
    const pct = taskProgress(task);
    progressBox.style.display = 'block';
    progress.value = String(pct);
    percent.textContent = `${Math.round(pct)}%`;
    setStatus(task.message || taskStatusText(task), task.status === 'failed' ? 'error' : '');
    detail.textContent = task.error_message || '';
    const active = !TERMINAL.has(task.status);
    cancelButton.style.display = active && task.cancellable ? '' : 'none';
    startButton.disabled = active;
    if (task.status === 'succeeded') {
      terminalTaskId = task.id || taskId;
      const stop = stopTask;
      stopTask = null;
      if (typeof stop === 'function') stop();
      startButton.disabled = false;
      setStartLabel('reprobe', t('ffmpeg.dialog.reprobe'));
      cancelButton.style.display = 'none';
      setStatus(t('ffmpeg.status.installed_recheck'), 'ok');
      Promise.resolve(refreshDeps()).then((deps) => {
        if (closed) return;
        if (deps?.ok === true) {
          setStatus(t('ffmpeg.status.installed_ok'), 'ok');
          toast(t('ffmpeg.status.installed_toast'), 'success');
        } else {
          setStatus(t('ffmpeg.status.installed_unresolved'), 'error');
        }
      }).catch(() => {
        if (!closed) setStatus(t('ffmpeg.status.installed_recheck_failed'), 'error');
      });
    } else if (TERMINAL.has(task.status)) {
      terminalTaskId = task.id || taskId;
      const stop = stopTask;
      stopTask = null;
      if (typeof stop === 'function') stop();
      startButton.disabled = false;
      setStartLabel('install', t('ffmpeg.status.retry_install'));
      if (task.status === 'cancelled') setStatus(t('ffmpeg.status.cancelled_msg'));
      else setStatus(task.error_message || task.message || taskStatusText(task), 'error');
    }
  };

  const attachTask = (id) => {
    if (!id || closed) return;
    taskId = id;
    terminalTaskId = null;
    const token = ++subscriptionToken;
    if (stopTask) stopTask();
    stopTask = null;
    Promise.resolve(subscribe(id, (payload) => updateTask(payload?.task)))
      .then((stop) => {
        if (closed || token !== subscriptionToken || terminalTaskId === id) {
          if (typeof stop === 'function') stop();
          return;
        }
        stopTask = stop;
      })
      .catch((error) => setStatus(t('ffmpeg.status.subscribe_failed', { detail: error.message }), 'error'));
  };

  const runReprobe = async (button) => {
    button.disabled = true;
    setStatus(t('ffmpeg.status.reprobe_start'));
    try {
      const deps = await refreshDeps();
      setStatus(deps?.ok === true ? t('ffmpeg.status.reprobe_ok') : t('ffmpeg.status.reprobe_missing'), deps?.ok === true ? 'ok' : 'error');
    } catch (error) {
      setStatus(t('ffmpeg.status.reprobe_failed', { detail: error.message }), 'error');
    } finally {
      button.disabled = false;
    }
  };

  const startInstall = async () => {
    startButton.disabled = true;
    setStartLabel('install', t('ffmpeg.status.installing_button'));
    progressBox.style.display = 'block';
    progress.value = '0';
    percent.textContent = '0%';
    detail.textContent = '';
    setStatus(t('ffmpeg.status.install_start'));
    try {
      const result = await apiFn('POST', FFMPEG_INSTALL_URL, {});
      attachTask(result?.task_id);
      if (result?.task) updateTask(result.task);
    } catch (error) {
      if (error.status === 409) {
        try {
          const current = await apiFn('GET', FFMPEG_INSTALL_STATUS_URL);
          if (current?.task_id) {
            updateTask({ ...current, id: current.task_id });
            attachTask(current.task_id);
            setStatus(t('ffmpeg.status.already_running'));
            return;
          }
        } catch { /* fall through to the visible error below */ }
      }
      startButton.disabled = false;
      setStartLabel('install', t('ffmpeg.status.retry_install'));
      setStatus(error.message || t('ffmpeg.status.start_failed'), 'error');
    }
  };

  const cancelInstall = async () => {
    if (!taskId) return;
    cancelButton.disabled = true;
    setStatus(t('ffmpeg.status.cancel_start'));
    try {
      await apiFn('POST', `/api/tasks/${encodeURIComponent(taskId)}/cancel`, {});
    } catch (error) {
      setStatus(t('ffmpeg.status.cancel_failed', { detail: error.message }), 'error');
      cancelButton.disabled = false;
    }
  };

  startButton.onclick = () => {
    if (startMode === 'reprobe') void runReprobe(startButton);
    else void startInstall();
  };
  cancelButton.onclick = () => void cancelInstall();
  $id('ffi-reprobe').onclick = () => void runReprobe($id('ffi-reprobe'));

  const close = () => {
    if (closed) return;
    closed = true;
    subscriptionToken += 1;
    if (stopTask) stopTask();
    stopTask = null;
    backdrop.remove();
  };
  backdrop.querySelector('.modal-backdrop').onclick = close;
  $id('ffi-close').onclick = close;

  const ready = Promise.resolve(apiFn('GET', FFMPEG_INSTALL_STATUS_URL))
    .then((current) => {
      if (closed) return;
      platform = current?.platform || platform;
      setAutomatic(current?.automatic !== false);
      appendGuidance($id('ffi-manual-body'), platform);
      if (current?.running && current.task_id) {
        updateTask({ ...current, id: current.task_id });
        attachTask(current.task_id);
      }
    })
    .catch(() => {
      if (closed) return;
      const automatic = platform === 'win32' || platform === 'linux';
      setAutomatic(automatic);
      appendGuidance($id('ffi-manual-body'), platform);
    });

  automaticSection.hidden = false;
  setAutomatic(platform === 'win32' || platform === 'linux');
  appendGuidance($id('ffi-manual-body'), platform);
  return { close, ready };
}
