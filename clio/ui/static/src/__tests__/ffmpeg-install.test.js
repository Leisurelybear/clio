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
    dialog.close();
    expect(stopped).toBe(true);
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

  it('disables automatic install when the backend reports no verified build', async () => {
    api.mockResolvedValueOnce({ status: 'idle', running: false, automatic: false, platform: 'darwin' });
    const dialog = openFfmpegInstallDialog({ platform: 'darwin' });
    await dialog.ready;

    expect(document.querySelector('#ffi-start').disabled).toBe(true);
    expect(document.querySelector('#ffi-manual-body').textContent).toContain('brew install ffmpeg');
    dialog.close();
  });
});
