import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({
  api: vi.fn(),
  icon: vi.fn((name) => `<span class="icon">${name}</span>`),
}));

import { api } from '../api.js';
import { applyLocale } from '../i18n.js';
import { formatFileSize, openServerPicker, pickerTitle } from '../file-browser.js';

async function flushPicker() {
  await vi.waitFor(() => {
    expect(document.querySelector('#fs-picker-entries')?.getAttribute('aria-busy')).not.toBe('true');
  });
}

describe('file-browser', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    api.mockReset();
  });

  afterEach(() => applyLocale('zh-CN'));

  it('formats titles and sizes', () => {
    expect(pickerTitle({ mode: 'folder' })).toBe('选择目录');
    expect(pickerTitle({ multiple: true, kind: 'video' })).toBe('选择视频');
    expect(formatFileSize(1024 * 1024)).toBe('1.0 MB');
  });

  it('selects one server-side file', async () => {
    api.mockResolvedValue({
      path: 'D:\\trip',
      parent: 'D:\\',
      dirs: [{ name: 'day1', path: 'D:\\trip\\day1' }],
      files: [{ name: 'clip.mp4', path: 'D:\\trip\\clip.mp4', size: 2048 }],
      is_drive_list: false,
    });
    const result = openServerPicker({ initialDir: 'D:\\trip', kind: 'video' });
    await flushPicker();

    document.querySelector('.fs-picker-file').click();
    document.querySelector('[data-fs-action="confirm"]').click();

    await expect(result).resolves.toBe('D:\\trip\\clip.mp4');
    expect(api).toHaveBeenCalledWith('GET', expect.stringContaining('/api/fs/entries?'));
  });

  it('selects the current directory', async () => {
    api.mockResolvedValue({
      path: 'D:\\trip',
      parent: 'D:\\',
      dirs: [],
      files: [],
      is_drive_list: false,
    });
    const result = openServerPicker({ initialDir: 'D:\\trip', mode: 'folder' });
    await flushPicker();

    document.querySelector('[data-fs-action="confirm"]').click();

    await expect(result).resolves.toBe('D:\\trip');
  });

  it('supports multiple file selection', async () => {
    api.mockResolvedValue({
      path: 'D:\\trip',
      parent: null,
      dirs: [],
      files: [
        { name: 'a.mp4', path: 'D:\\trip\\a.mp4', size: 1 },
        { name: 'b.mp4', path: 'D:\\trip\\b.mp4', size: 1 },
      ],
      is_drive_list: false,
    });
    const result = openServerPicker({ initialDir: 'D:\\trip', kind: 'video', multiple: true });
    await flushPicker();

    document.querySelectorAll('.fs-picker-file').forEach(button => button.click());
    document.querySelector('[data-fs-action="confirm"]').click();

    await expect(result).resolves.toEqual(['D:\\trip\\a.mp4', 'D:\\trip\\b.mp4']);
  });

  it('preselects an initial file returned by the server', async () => {
    api.mockResolvedValue({
      path: 'D:\\trip',
      parent: 'D:\\',
      dirs: [],
      files: [{ name: 'clip.mp4', path: 'D:\\trip\\clip.mp4', size: 1 }],
      selected_path: 'D:\\trip\\clip.mp4',
      is_drive_list: false,
    });
    const result = openServerPicker({
      initialDir: 'D:\\trip\\clip.mp4',
      kind: 'video',
      scope: 'project',
    });
    await flushPicker();

    expect(document.querySelector('.fs-picker-file').classList.contains('selected')).toBe(true);
    expect(document.querySelector('[data-fs-action="confirm"]').disabled).toBe(false);
    document.querySelector('[data-fs-action="confirm"]').click();

    await expect(result).resolves.toBe('D:\\trip\\clip.mp4');
    expect(api).toHaveBeenCalledWith('GET', expect.stringContaining('scope=project'));
  });

  it('filters entries by name and confirms the keyboard-highlighted file', async () => {
    api.mockResolvedValue({
      path: 'D:\\trip',
      parent: 'D:\\',
      dirs: [{ name: 'day1', path: 'D:\\trip\\day1' }],
      files: [
        { name: 'clip_a.mp4', path: 'D:\\trip\\clip_a.mp4', size: 1 },
        { name: 'clip_b.mp4', path: 'D:\\trip\\clip_b.mp4', size: 1 },
      ],
      is_drive_list: false,
    });
    const result = openServerPicker({ initialDir: 'D:\\trip', kind: 'video' });
    await flushPicker();

    const filter = document.querySelector('#fs-picker-filter');
    filter.value = 'b';
    filter.dispatchEvent(new Event('input'));
    expect(document.querySelectorAll('.fs-picker-file')).toHaveLength(1);
    expect(document.querySelector('.fs-picker-file').dataset.path).toBe('D:\\trip\\clip_b.mp4');

    filter.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown' }));
    expect(document.querySelector('.fs-picker-entry.active')).not.toBeNull();
    filter.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));

    await expect(result).resolves.toBe('D:\\trip\\clip_b.mp4');
  });

  it('reports an empty state when the filter excludes every entry', async () => {
    api.mockResolvedValue({
      path: 'D:\\trip',
      parent: 'D:\\',
      dirs: [],
      files: [{ name: 'clip.mp4', path: 'D:\\trip\\clip.mp4', size: 1 }],
      is_drive_list: false,
    });
    openServerPicker({ initialDir: 'D:\\trip', kind: 'video' });
    await flushPicker();

    const filter = document.querySelector('#fs-picker-filter');
    filter.value = 'zzz';
    filter.dispatchEvent(new Event('input'));
    expect(document.querySelector('.fs-picker-empty')).not.toBeNull();

    document.querySelector('[data-fs-action="cancel"]').click();
  });

  it('activates the first entry when Enter is pressed with no highlight', async () => {
    api.mockResolvedValue({
      path: 'D:\\trip',
      parent: 'D:\\',
      dirs: [],
      files: [
        { name: 'a.mp4', path: 'D:\\trip\\a.mp4', size: 1 },
        { name: 'b.mp4', path: 'D:\\trip\\b.mp4', size: 1 },
      ],
      is_drive_list: false,
    });
    const result = openServerPicker({ initialDir: 'D:\\trip', kind: 'video' });
    await flushPicker();

    document.querySelector('#fs-picker-filter')
      .dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));

    await expect(result).resolves.toBe('D:\\trip\\a.mp4');
  });

  it('highlights the last entry on ArrowUp from the filter and exposes aria-activedescendant', async () => {
    api.mockResolvedValue({
      path: 'D:\\trip',
      parent: 'D:\\',
      dirs: [],
      files: [
        { name: 'a.mp4', path: 'D:\\trip\\a.mp4', size: 1 },
        { name: 'b.mp4', path: 'D:\\trip\\b.mp4', size: 1 },
      ],
      is_drive_list: false,
    });
    openServerPicker({ initialDir: 'D:\\trip', kind: 'video' });
    await flushPicker();

    document.querySelector('#fs-picker-filter')
      .dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp' }));
    const active = document.querySelector('.fs-picker-entry.active');
    expect(active?.dataset.path).toBe('D:\\trip\\b.mp4');
    expect(document.querySelector('#fs-picker-entries').getAttribute('aria-activedescendant')).toBe(active.id);

    document.querySelector('[data-fs-action="cancel"]').click();
  });

  it('keeps entry buttons out of the tab order', async () => {
    api.mockResolvedValue({
      path: 'D:\\trip',
      parent: 'D:\\',
      dirs: [],
      files: [{ name: 'a.mp4', path: 'D:\\trip\\a.mp4', size: 1 }],
      is_drive_list: false,
    });
    openServerPicker({ initialDir: 'D:\\trip', kind: 'video' });
    await flushPicker();

    expect(document.querySelector('.fs-picker-entry').tabIndex).toBe(-1);

    document.querySelector('[data-fs-action="cancel"]').click();
  });

  it('navigates into a directory selected with the keyboard', async () => {
    api.mockResolvedValue({
      path: 'D:\\trip',
      parent: 'D:\\',
      dirs: [{ name: 'day1', path: 'D:\\trip\\day1' }],
      files: [],
      is_drive_list: false,
    });
    openServerPicker({ initialDir: 'D:\\trip', mode: 'folder' });
    await flushPicker();

    document.querySelector('#fs-picker-filter')
      .dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));

    await vi.waitFor(() => {
      expect(api).toHaveBeenCalledWith(
        'GET',
        expect.stringContaining(encodeURIComponent('D:\\trip\\day1')),
      );
    });

    document.querySelector('[data-fs-action="cancel"]').click();
  });

  it('refreshes the picker chrome when the locale changes', async () => {
    api.mockResolvedValue({
      path: 'D:\\trip',
      parent: 'D:\\',
      dirs: [],
      files: [],
      is_drive_list: false,
    });
    openServerPicker({ initialDir: 'D:\\trip', kind: 'video' });
    await flushPicker();

    applyLocale('en');

    expect(document.querySelector('[data-fs-action="mkdir"]').textContent).toBe('New folder');
    expect(document.querySelector('[data-fs-action="go"]').textContent).toBe('Go');
    expect(document.querySelector('#fs-picker-title').textContent).toBe('Select files');

    document.querySelector('[data-fs-action="cancel"]').click();
  });
});
