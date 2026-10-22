import { api } from './api.js';
import { subscribeTask } from './task-center.js';
import { addToast } from './toast.js';

export const FFMPEG_INSTALL_STATUS_URL = '/api/deps/ffmpeg/install/status';
export const FFMPEG_INSTALL_URL = '/api/deps/ffmpeg/install';

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'interrupted']);

const STATUS_TEXT = {
  queued: '等待安装…',
  running: '正在安装…',
  cancelling: '正在取消…',
  succeeded: '安装完成',
  failed: '安装失败',
  cancelled: '安装已取消',
  interrupted: '安装中断',
};

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
        '打开终端，运行 brew install ffmpeg（需要先安装 Homebrew：https://brew.sh/）。',
        '安装完成后回到设置 → 全局，点击「重新检测」。',
        '如果仍检测不到，可在 paths.ffmpeg / paths.ffprobe 填写完整路径。',
      ],
      downloadUrl: 'https://ffmpeg.org/download.html#build-mac',
      downloadLabel: '打开手动下载页',
    };
  }
  if (platform === 'linux') {
    return {
      steps: [
        '使用发行版包管理器安装：Debian/Ubuntu 运行 sudo apt install ffmpeg；Fedora 运行 sudo dnf install ffmpeg。',
        '安装完成后回到设置 → 全局，点击「重新检测」。',
        '如果仍检测不到，可在 paths.ffmpeg / paths.ffprobe 填写完整路径。',
      ],
      downloadUrl: 'https://ffmpeg.org/download.html#build-linux',
      downloadLabel: '打开手动下载页',
    };
  }
  if (platform === 'win32') {
    return {
      steps: [
        '使用包管理器安装：winget install Gyan.FFmpeg，或 choco install ffmpeg。',
        '也可以下载含 ffprobe 的 Windows 构建，解压后把 bin 目录加入 PATH。',
        '配置完成后回到设置 → 全局，点击「重新检测」。',
      ],
      downloadUrl: 'https://www.gyan.dev/ffmpeg/builds/',
      downloadLabel: '打开下载页',
    };
  }
  return {
    steps: [
      '请从 ffmpeg.org 下载适合当前系统的包含 ffprobe 的构建。',
      '把 ffmpeg 与 ffprobe 所在目录加入 PATH。',
      '配置完成后回到设置 → 全局，点击「重新检测」。',
    ],
    downloadUrl: 'https://ffmpeg.org/download.html',
    downloadLabel: '打开下载页',
  };
}

export function taskProgress(task) {
  const value = Number(task?.progress_pct);
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

export function taskStatusText(task) {
  const status = task?.status || 'queued';
  return STATUS_TEXT[status] || status;
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
      <h3 style="margin:0 0 var(--space-4)">安装 ffmpeg / ffprobe</h3>
      <p class="muted" style="margin:0 0 12px">压缩、波形、裁剪、转录抽音都依赖这两个命令行工具。自动安装会下载 BtbN 的静态构建并校验 SHA256，只写入 Clio 管理目录。</p>
      <section id="ffi-auto">
        <p id="ffi-auto-note" class="muted" style="font-size:var(--text-xs);margin:0 0 8px"></p>
        <div class="modal-actions" style="justify-content:flex-start;margin:0 0 10px">
          <button id="ffi-start" class="btn-primary">自动安装</button>
          <button id="ffi-cancel" class="btn-secondary" style="display:none">取消安装</button>
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
        <summary style="cursor:pointer;font-size:var(--text-sm)">手动安装</summary>
        <div id="ffi-manual-body" style="margin-top:10px;font-size:var(--text-sm)"></div>
      </details>
      <p class="muted" style="font-size:var(--text-xs);margin:0">自动安装不会修改 config.yaml 中已经显式填写的 paths.ffmpeg / paths.ffprobe。</p>
      <div class="modal-actions" style="margin-top:16px">
        <button id="ffi-reprobe" class="btn-secondary">重新检测</button>
        <button id="ffi-close" class="btn-secondary">关闭</button>
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

  const setAutomatic = (automatic) => {
    startButton.disabled = !automatic;
    startButton.title = automatic ? '' : '当前平台请使用手动安装';
    autoNote.textContent = automatic
      ? '下载约需数分钟，进度会显示在下方。'
      : '当前平台没有经过校验的一键安装包，请按下方手动步骤安装。';
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
      startButton.textContent = '重新检测';
      cancelButton.style.display = 'none';
      setStatus('安装完成，正在重新检测…', 'ok');
      Promise.resolve(refreshDeps()).then((deps) => {
        if (closed) return;
        if (deps?.ok === true) {
          setStatus('ffmpeg / ffprobe 已安装并通过检测。', 'ok');
          toast('ffmpeg / ffprobe 安装完成', 'success');
        } else {
          setStatus('安装完成，但当前配置路径仍无法解析 ffmpeg/ffprobe，请检查路径设置。', 'error');
        }
      }).catch(() => {
        if (!closed) setStatus('安装完成，但重新检测失败，请点击「重新检测」重试。', 'error');
      });
    } else if (TERMINAL.has(task.status)) {
      terminalTaskId = task.id || taskId;
      const stop = stopTask;
      stopTask = null;
      if (typeof stop === 'function') stop();
      startButton.disabled = false;
      startButton.textContent = '重试安装';
      if (task.status === 'cancelled') setStatus('安装已取消。');
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
      .catch((error) => setStatus(`无法订阅安装进度：${error.message}`, 'error'));
  };

  const startInstall = async () => {
    if (startButton.textContent === '重新检测') {
      startButton.disabled = true;
      setStatus('正在重新检测…');
      try {
        const deps = await refreshDeps();
        setStatus(deps?.ok === true ? '检测完成。' : '仍未检测到可用的 ffmpeg/ffprobe。', deps?.ok === true ? 'ok' : 'error');
      } catch (error) {
        setStatus(`重新检测失败：${error.message}`, 'error');
      } finally {
        startButton.disabled = false;
      }
      return;
    }
    startButton.disabled = true;
    startButton.textContent = '安装中…';
    progressBox.style.display = 'block';
    progress.value = '0';
    percent.textContent = '0%';
    detail.textContent = '';
    setStatus('正在启动安装任务…');
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
            setStatus('已有安装任务正在运行。');
            return;
          }
        } catch { /* fall through to the visible error below */ }
      }
      startButton.disabled = false;
      startButton.textContent = '重试安装';
      setStatus(error.message || '无法启动安装任务', 'error');
    }
  };

  const cancelInstall = async () => {
    if (!taskId) return;
    cancelButton.disabled = true;
    setStatus('正在取消安装…');
    try {
      await apiFn('POST', `/api/tasks/${encodeURIComponent(taskId)}/cancel`, {});
    } catch (error) {
      setStatus(`取消失败：${error.message}`, 'error');
      cancelButton.disabled = false;
    }
  };

  startButton.onclick = () => void startInstall();
  cancelButton.onclick = () => void cancelInstall();
  $id('ffi-reprobe').onclick = async () => {
    $id('ffi-reprobe').disabled = true;
    setStatus('正在重新检测…');
    try {
      const deps = await refreshDeps();
      setStatus(deps?.ok === true ? '检测完成。' : '仍未检测到可用的 ffmpeg/ffprobe。', deps?.ok === true ? 'ok' : 'error');
    } catch (error) {
      setStatus(`重新检测失败：${error.message}`, 'error');
    } finally {
      $id('ffi-reprobe').disabled = false;
    }
  };

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
