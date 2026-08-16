import { describe, it, expect, beforeEach, vi } from 'vitest';
import { openServerPicker } from '../file-browser.js';
import {
  isDesktop,
  pickFolder,
  applyPickToInput,
  setBrowseButtonsVisible,
  initDesktopPickers,
} from '../desktop-pick.js';

vi.mock('../file-browser.js', () => ({
  openServerPicker: vi.fn(),
}));

describe('desktop-pick', () => {
  beforeEach(() => {
    delete window.pywebview;
    openServerPicker.mockReset();
    document.body.innerHTML = `
      <input id="p" value="old" />
      <button class="browse-btn" type="button">浏览</button>
    `;
  });

  it('isDesktop false without pywebview', () => {
    expect(isDesktop()).toBe(false);
  });

  it('isDesktop true with api', () => {
    window.pywebview = { api: { pick_folder: vi.fn() } };
    expect(isDesktop()).toBe(true);
  });

  it('pickFolder returns path on ok', async () => {
    window.pywebview = {
      api: {
        pick_folder: vi.fn(async () => ({ ok: true, path: 'D:\\\\trip' })),
      },
    };
    await expect(pickFolder('D:\\\\')).resolves.toBe('D:\\\\trip');
  });

  it('pickFolder returns null on cancel', async () => {
    window.pywebview = {
      api: {
        pick_folder: vi.fn(async () => ({ ok: false, cancelled: true })),
      },
    };
    await expect(pickFolder()).resolves.toBeNull();
  });

  it('pickFolder uses the server browser when not desktop', async () => {
    openServerPicker.mockResolvedValue('D:\\server-trip');
    await expect(pickFolder('D:\\')).resolves.toBe('D:\\server-trip');
    expect(openServerPicker).toHaveBeenCalledWith({
      initialDir: 'D:\\',
      mode: 'folder',
      kind: 'any',
      scope: 'project',
    });
  });

  it('passes config scope and project context to the desktop bridge', async () => {
    const pick = vi.fn(async () => ({ ok: true, path: 'C:\\logs' }));
    window.pywebview = { api: { pick_folder: pick } };

    await pickFolder('./logs', { scope: 'config', projectDir: 'D:\\project' });

    expect(pick).toHaveBeenCalledWith('./logs', 'config', 'D:\\project');
  });

  it('applyPickToInput writes only non-null', () => {
    const inp = document.getElementById('p');
    expect(applyPickToInput(inp, null)).toBe(false);
    expect(inp.value).toBe('old');
    expect(applyPickToInput(inp, 'D:\\\\x')).toBe(true);
    expect(inp.value).toBe('D:\\\\x');
  });

  it('setBrowseButtonsVisible keeps browse available in serve mode', () => {
    setBrowseButtonsVisible(document);
    expect(document.querySelector('.browse-btn').style.display).not.toBe('none');
    expect(document.querySelector('.browse-btn').title).toMatch(/Clio/);
  });

  it('initDesktopPickers reveals buttons once pywebview bridge arrives', () => {
    initDesktopPickers(document);
    expect(document.querySelector('.browse-btn').style.display).not.toBe('none');

    window.pywebview = { api: { pick_folder: vi.fn() } };
    window.dispatchEvent(new Event('pywebviewready'));
    expect(document.querySelector('.browse-btn').style.display).not.toBe('none');
  });
});
