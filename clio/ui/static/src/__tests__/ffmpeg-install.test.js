import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({ api: vi.fn() }));
vi.mock('../task-center.js', () => ({ subscribeTask: vi.fn() }));
vi.mock('../toast.js', () => ({ addToast: vi.fn() }));

import { api } from '../api.js';
import { subscribeTask } from '../task-center.js';
import { addToast } from '../toast.js';
import {
  detectPlatform,
  manualInstallGuidance,
  openFfmpegInstallDialog,
  taskProgress,
  taskStatusText,
} from '../ffmpeg-install.js';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('ffmpeg install helpers', () => {
  afterEach(() => {
    document.body.replaceChildren();
  });

  it('detects the three supported desktop platforms', () => {
    expect(detectPlatform('Mozilla/5.0 (Windows NT 10.0; Win64; x64)')).toBe('win32');
    expect(detectPlatform('Mozilla/5.0 (Macintosh; Intel Mac OS X)')).toBe('darwin');
    expect(detectPlatform('Mozilla/5.0 (X11; Linux x86_64)')).toBe('linux');
    expect(detectPlatform('CustomOS')).toBe('unknown');
  });

  it('provides platform-specific manual fallback guidance', () => {
    expect(manualInstallGuidance('darwin').steps[0]).toContain('brew install ffmpeg');
    expect(manualInstallGuidance('linux').steps[0]).toContain('apt install ffmpeg');
    expect(manualInstallGuidance('win32').downloadUrl).toContain('gyan.dev');
  });

  it('clamps progress and labels task states', () => {
    expect(taskProgress({ progress_pct: 58.4 })).toBe(58.4);
    expect(taskProgress({ progress_pct: 140 })).toBe(100);
    expect(taskProgress({ progress_pct: -2 })).toBe(0);
    expect(taskProgress({})).toBe(0);
    expect(taskStatusText({ status: 'cancelling' })).toBe('正在取消…');
  });
});

describe('ffmpeg install dialog', () => {
  beforeEach(() => {
    api.mockReset();
    subscribeTask.mockReset();
    addToast.mockReset();
  });

  afterEach(() => {
    document.body.replaceChildren();
  });

  it('starts a task, follows progress, and refreshes dependencies on success', async () => {
    let listener;
    let stopped = false;
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: true, platform: 'win32' });
    api.mockResolvedValueOnce({ ok: true, task_id: 'task-1' });
    subscribeTask.mockImplementation((id, callback) => {
      expect(id).toBe('task-1');
      listener = callback;
      return () => { stopped = true; };
    });
    const refreshDeps = vi.fn(async () => ({ ok: true }));
    const dialog = openFfmpegInstallDialog({ platform: 'win32', refreshDeps });
    await dialog.ready;

    document.querySelector('#ffi-start').click();
    await flush();
    expect(api).toHaveBeenCalledWith('POST', '/api/deps/ffmpeg/install', {});
    expect(subscribeTask).toHaveBeenCalledWith('task-1', expect.any(Function));

    listener({ task: { id: 'task-1', status: 'running', progress_pct: 25, message: 'Downloading', cancellable: true } });
    expect(document.querySelector('#ffi-progress').value).toBe(25);
    expect(document.querySelector('#ffi-cancel').style.display).toBe('');

    listener({ task: { id: 'task-1', status: 'succeeded', progress_pct: 100 } });
    await flush();
    expect(refreshDeps).toHaveBeenCalled();
    expect(addToast).toHaveBeenCalledWith('ffmpeg / ffprobe 安装完成', 'success');
    expect(stopped).toBe(true);
    dialog.close();
  });

  it('does not report success when the post-install probe still fails', async () => {
    let listener;
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: true, platform: 'win32' });
    api.mockResolvedValueOnce({ ok: true, task_id: 'task-2' });
    subscribeTask.mockImplementation((_id, callback) => {
      listener = callback;
      return () => {};
    });
    const refreshDeps = vi.fn(async () => ({ ok: false }));
    const dialog = openFfmpegInstallDialog({ platform: 'win32', refreshDeps });
    await dialog.ready;

    document.querySelector('#ffi-start').click();
    await flush();
    listener({ task: { id: 'task-2', status: 'succeeded', progress_pct: 100 } });
    await flush();

    expect(refreshDeps).toHaveBeenCalled();
    expect(addToast).not.toHaveBeenCalled();
    expect(document.querySelector('#ffi-status').textContent).toContain('仍无法解析');
    dialog.close();
  });

  it('handles duplicate terminal events only once', async () => {
    let listener;
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: true, platform: 'win32' });
    api.mockResolvedValueOnce({ ok: true, task_id: 'task-3' });
    subscribeTask.mockImplementation((_id, callback) => {
      listener = callback;
      return () => {};
    });
    const refreshDeps = vi.fn(async () => ({ ok: true }));
    const dialog = openFfmpegInstallDialog({ platform: 'win32', refreshDeps });
    await dialog.ready;

    document.querySelector('#ffi-start').click();
    await flush();
    const success = { task: { id: 'task-3', status: 'succeeded', progress_pct: 100 } };
    listener(success);
    listener(success);
    await flush();

    expect(refreshDeps).toHaveBeenCalledTimes(1);
    expect(addToast).toHaveBeenCalledTimes(1);
    dialog.close();
  });

  it('applies an already-running task to the dialog immediately', async () => {
    api.mockResolvedValueOnce({
      status: 'running',
      running: true,
      task_id: 'task-9',
      progress_pct: 42,
      message: 'Downloading',
      cancellable: true,
      automatic: true,
      platform: 'win32',
    });
    const subscribe = vi.fn(() => () => {});
    const dialog = openFfmpegInstallDialog({ platform: 'win32', subscribe });
    await dialog.ready;

    expect(document.querySelector('#ffi-start').disabled).toBe(true);
    expect(document.querySelector('#ffi-cancel').style.display).toBe('');
    expect(Number(document.querySelector('#ffi-progress').value)).toBe(42);
    expect(subscribe).toHaveBeenCalledWith('task-9', expect.any(Function));
    dialog.close();
  });

  it('applies the active task when start returns 409', async () => {
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: true, platform: 'win32' });
    const conflict = new Error('conflict');
    conflict.status = 409;
    api.mockRejectedValueOnce(conflict);
    api.mockResolvedValueOnce({
      status: 'running',
      running: true,
      task_id: 'task-10',
      progress_pct: 55,
      message: 'Downloading',
      cancellable: true,
      automatic: true,
      platform: 'win32',
    });
    const subscribe = vi.fn(() => () => {});
    const dialog = openFfmpegInstallDialog({ platform: 'win32', subscribe });
    await dialog.ready;

    document.querySelector('#ffi-start').click();
    await flush();
    await flush();

    expect(document.querySelector('#ffi-start').disabled).toBe(true);
    expect(document.querySelector('#ffi-cancel').style.display).toBe('');
    expect(Number(document.querySelector('#ffi-progress').value)).toBe(55);
    expect(subscribe).toHaveBeenCalledWith('task-10', expect.any(Function));
    dialog.close();
  });

  it('disables automatic install when the backend reports no verified build', async () => {
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: false, platform: 'darwin' });
    const dialog = openFfmpegInstallDialog({ platform: 'darwin' });
    await dialog.ready;

    expect(document.querySelector('#ffi-start').disabled).toBe(true);
    expect(document.querySelector('#ffi-manual-body').textContent).toContain('brew install ffmpeg');
    dialog.close();
  });

  it('falls back to generic guidance for unknown platforms', () => {
    expect(manualInstallGuidance('unknown').downloadUrl).toContain('ffmpeg.org');
  });

  it('reports a failed task and allows a retry', async () => {
    let listener;
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: true, platform: 'win32' });
    api.mockResolvedValueOnce({ ok: true, task_id: 'task-f' });
    subscribeTask.mockImplementation((_id, callback) => { listener = callback; return () => {}; });
    const dialog = openFfmpegInstallDialog({ platform: 'win32' });
    await dialog.ready;

    document.querySelector('#ffi-start').click();
    await flush();
    listener({});
    listener({ task: { id: 'task-f', status: 'failed', error_message: '下载失败' } });

    expect(document.querySelector('#ffi-status').textContent).toContain('下载失败');
    expect(document.querySelector('#ffi-start').textContent).toBe('重试安装');
    expect(document.querySelector('#ffi-start').disabled).toBe(false);
    dialog.close();
  });

  it('shows a cancellation message for a cancelled task', async () => {
    let listener;
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: true, platform: 'win32' });
    api.mockResolvedValueOnce({ ok: true, task_id: 'task-c' });
    subscribeTask.mockImplementation((_id, callback) => { listener = callback; return () => {}; });
    const dialog = openFfmpegInstallDialog({ platform: 'win32' });
    await dialog.ready;
    document.querySelector('#ffi-start').click();
    await flush();

    listener({ task: { id: 'task-c', status: 'cancelled' } });

    expect(document.querySelector('#ffi-status').textContent).toContain('已取消');
    dialog.close();
  });

  it('ignores a start response without a task id', async () => {
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: true, platform: 'win32' });
    api.mockResolvedValueOnce({ ok: true });
    const subscribe = vi.fn();
    const dialog = openFfmpegInstallDialog({ platform: 'win32', subscribe });
    await dialog.ready;

    document.querySelector('#ffi-start').click();
    await flush();

    expect(subscribe).not.toHaveBeenCalled();
    expect(document.querySelector('#ffi-status').textContent).toContain('正在启动安装任务');
    dialog.close();
  });

  it('applies a task object returned by the start call', async () => {
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: true, platform: 'win32' });
    api.mockResolvedValueOnce({
      ok: true,
      task_id: 'task-o',
      task: { id: 'task-o', status: 'running', progress_pct: 33, message: 'Downloading', cancellable: true },
    });
    const subscribe = vi.fn(() => () => {});
    const dialog = openFfmpegInstallDialog({ platform: 'win32', subscribe });
    await dialog.ready;
    document.querySelector('#ffi-start').click();
    await flush();

    expect(Number(document.querySelector('#ffi-progress').value)).toBe(33);
    expect(document.querySelector('#ffi-cancel').style.display).toBe('');
    dialog.close();
  });

  it('reports a failed install start', async () => {
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: true, platform: 'win32' });
    api.mockRejectedValueOnce(new Error('boom'));
    const dialog = openFfmpegInstallDialog({ platform: 'win32' });
    await dialog.ready;

    document.querySelector('#ffi-start').click();
    await flush();

    expect(document.querySelector('#ffi-status').textContent).toContain('boom');
    expect(document.querySelector('#ffi-start').textContent).toBe('重试安装');
    dialog.close();
  });

  it('re-runs detection from the start button after a successful install', async () => {
    let listener;
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: true, platform: 'win32' });
    api.mockResolvedValueOnce({ ok: true, task_id: 'task-r' });
    subscribeTask.mockImplementation((_id, callback) => { listener = callback; return () => {}; });
    const refreshDeps = vi.fn(async () => ({ ok: true }));
    const dialog = openFfmpegInstallDialog({ platform: 'win32', refreshDeps });
    await dialog.ready;
    document.querySelector('#ffi-start').click();
    await flush();
    listener({ task: { id: 'task-r', status: 'succeeded', progress_pct: 100 } });
    await flush();
    expect(refreshDeps).toHaveBeenCalledTimes(1);

    document.querySelector('#ffi-start').click();
    await flush();

    expect(refreshDeps).toHaveBeenCalledTimes(2);
    expect(document.querySelector('#ffi-status').textContent).toContain('检测完成');
    dialog.close();
  });

  it('skips the post-install status update when the dialog is closed', async () => {
    let listener;
    let resolveDeps;
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: true, platform: 'win32' });
    api.mockResolvedValueOnce({ ok: true, task_id: 'task-z' });
    subscribeTask.mockImplementation((_id, callback) => { listener = callback; return () => {}; });
    const refreshDeps = vi.fn(() => new Promise(resolve => { resolveDeps = resolve; }));
    const dialog = openFfmpegInstallDialog({ platform: 'win32', refreshDeps });
    await dialog.ready;
    document.querySelector('#ffi-start').click();
    await flush();
    listener({ task: { id: 'task-z', status: 'succeeded', progress_pct: 100 } });

    dialog.close();
    resolveDeps({ ok: true });
    await flush();

    expect(addToast).not.toHaveBeenCalled();
  });

  it('reports a failed post-install recheck', async () => {
    let listener;
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: true, platform: 'win32' });
    api.mockResolvedValueOnce({ ok: true, task_id: 'task-w' });
    subscribeTask.mockImplementation((_id, callback) => { listener = callback; return () => {}; });
    const refreshDeps = vi.fn(async () => { throw new Error('probe down'); });
    const dialog = openFfmpegInstallDialog({ platform: 'win32', refreshDeps });
    await dialog.ready;
    document.querySelector('#ffi-start').click();
    await flush();
    listener({ task: { id: 'task-w', status: 'succeeded', progress_pct: 100 } });
    await flush();
    await flush();

    expect(document.querySelector('#ffi-status').textContent).toContain('重新检测失败');
    dialog.close();
  });

  it('cancels the running task', async () => {
    let listener;
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: true, platform: 'win32' });
    api.mockResolvedValueOnce({ ok: true, task_id: 'task-x' });
    api.mockResolvedValueOnce({ ok: true });
    subscribeTask.mockImplementation((_id, callback) => { listener = callback; return () => {}; });
    const dialog = openFfmpegInstallDialog({ platform: 'win32' });
    await dialog.ready;
    document.querySelector('#ffi-start').click();
    await flush();
    listener({ task: { id: 'task-x', status: 'running', progress_pct: 10, cancellable: true } });

    document.querySelector('#ffi-cancel').click();
    await flush();

    expect(api).toHaveBeenCalledWith('POST', '/api/tasks/task-x/cancel', {});
    dialog.close();
  });

  it('reports a cancellation failure', async () => {
    let listener;
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: true, platform: 'win32' });
    api.mockResolvedValueOnce({ ok: true, task_id: 'task-y' });
    api.mockRejectedValueOnce(new Error('nope'));
    subscribeTask.mockImplementation((_id, callback) => { listener = callback; return () => {}; });
    const dialog = openFfmpegInstallDialog({ platform: 'win32' });
    await dialog.ready;
    document.querySelector('#ffi-start').click();
    await flush();
    listener({ task: { id: 'task-y', status: 'running', progress_pct: 10, cancellable: true } });

    document.querySelector('#ffi-cancel').click();
    await flush();

    expect(document.querySelector('#ffi-status').textContent).toContain('取消失败');
    expect(document.querySelector('#ffi-cancel').disabled).toBe(false);
    dialog.close();
  });

  it('reports when subscribing to install progress fails', async () => {
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: true, platform: 'win32' });
    api.mockResolvedValueOnce({ ok: true, task_id: 'task-s' });
    subscribeTask.mockImplementation(() => Promise.reject(new Error('no sse')));
    const dialog = openFfmpegInstallDialog({ platform: 'win32' });
    await dialog.ready;
    document.querySelector('#ffi-start').click();
    await flush();
    await flush();

    expect(document.querySelector('#ffi-status').textContent).toContain('无法订阅安装进度');
    dialog.close();
  });

  it('still renders manual guidance when the status probe fails', async () => {
    api.mockRejectedValueOnce(new Error('offline'));
    const dialog = openFfmpegInstallDialog({ platform: 'linux' });
    await dialog.ready;

    expect(document.querySelector('#ffi-manual-body').textContent).toContain('apt install ffmpeg');
    expect(document.querySelector('#ffi-start').disabled).toBe(false);
    dialog.close();
  });

  it('reprobes dependencies from the manual section', async () => {
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: false, platform: 'darwin' });
    const dialog = openFfmpegInstallDialog({ platform: 'darwin' });
    await dialog.ready;

    document.querySelector('#ffi-reprobe').click();
    await flush();

    expect(document.querySelector('#ffi-status').textContent).toContain('仍未检测到');
    expect(document.querySelector('#ffi-reprobe').disabled).toBe(false);
    dialog.close();
  });

  it('reports a failed manual reprobe', async () => {
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: false, platform: 'darwin' });
    const refreshDeps = vi.fn(async () => { throw new Error('probe boom'); });
    const dialog = openFfmpegInstallDialog({ platform: 'darwin', refreshDeps });
    await dialog.ready;

    document.querySelector('#ffi-reprobe').click();
    await flush();

    expect(document.querySelector('#ffi-status').textContent).toContain('重新检测失败');
    dialog.close();
  });

  it('closes the dialog from the backdrop only once', async () => {
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: true, platform: 'win32' });
    const dialog = openFfmpegInstallDialog({ platform: 'win32' });
    await dialog.ready;

    const closeButton = document.querySelector('#ffi-close');
    document.querySelector('.modal-backdrop').click();
    closeButton.click();

    expect(document.querySelector('.modal')).toBeNull();
  });

  it('ignores a cancel click before any task starts', async () => {
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: true, platform: 'win32' });
    const dialog = openFfmpegInstallDialog({ platform: 'win32' });
    await dialog.ready;

    document.querySelector('#ffi-cancel').click();
    await flush();

    expect(api).toHaveBeenCalledTimes(1);
    dialog.close();
  });

  it('reports a failed recheck from the start button', async () => {
    let listener;
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: true, platform: 'win32' });
    api.mockResolvedValueOnce({ ok: true, task_id: 'task-q' });
    subscribeTask.mockImplementation((_id, callback) => { listener = callback; return () => {}; });
    const refreshDeps = vi.fn(async () => ({ ok: true }));
    const dialog = openFfmpegInstallDialog({ platform: 'win32', refreshDeps });
    await dialog.ready;
    document.querySelector('#ffi-start').click();
    await flush();
    listener({ task: { id: 'task-q', status: 'succeeded', progress_pct: 100 } });
    await flush();

    refreshDeps.mockRejectedValueOnce(new Error('recheck down'));
    document.querySelector('#ffi-start').click();
    await flush();

    expect(document.querySelector('#ffi-status').textContent).toContain('重新检测失败');
    dialog.close();
  });

  it('does not render guidance after the dialog is closed before status resolves', async () => {
    let resolveStatus;
    api.mockImplementationOnce(() => new Promise(resolve => { resolveStatus = resolve; }));
    const dialog = openFfmpegInstallDialog({ platform: 'win32' });
    dialog.close();
    resolveStatus({ status: 'idle', running: false, automatic: true, platform: 'win32' });
    await dialog.ready;

    expect(document.querySelector('.modal')).toBeNull();
  });

  it('does not render guidance when closed before a failing status resolves', async () => {
    let rejectStatus;
    api.mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectStatus = reject; }));
    const dialog = openFfmpegInstallDialog({ platform: 'win32' });
    dialog.close();
    rejectStatus(new Error('down'));
    await dialog.ready;

    expect(document.querySelector('.modal')).toBeNull();
  });
});
