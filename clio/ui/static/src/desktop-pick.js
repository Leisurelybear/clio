import { openServerPicker } from './file-browser.js';
import { t } from './i18n.js';

export function isDesktop() {
  return !!(window.pywebview && window.pywebview.api);
}

export async function pickFolder(initialDir = '', { scope = 'project', projectDir = '' } = {}) {
  const api = window.pywebview?.api;
  if (api?.pick_folder) {
    const r = await api.pick_folder(initialDir || '', scope, projectDir);
    if (!r || r.cancelled) return null;
    if (!r.ok) throw new Error(r.error || t('picker.folder_failed'));
    return r.path || null;
  }
  return openServerPicker({ initialDir, mode: 'folder', kind: 'any', scope });
}

export async function pickFile(initialDir = '', kind = 'video', { scope = 'project', projectDir = '' } = {}) {
  const api = window.pywebview?.api;
  if (api?.pick_file) {
    const r = await api.pick_file(initialDir || '', kind, scope, projectDir);
    if (!r || r.cancelled) return null;
    if (!r.ok) throw new Error(r.error || t('picker.file_failed'));
    return r.path || null;
  }
  return openServerPicker({ initialDir, mode: 'file', kind, multiple: false, scope });
}

export async function pickFiles(initialDir = '', kind = 'video', { scope = 'project', projectDir = '' } = {}) {
  const api = window.pywebview?.api;
  if (api?.pick_files) {
    const r = await api.pick_files(initialDir || '', kind, scope, projectDir);
    if (!r || r.cancelled) return null;
    if (!r.ok) throw new Error(r.error || t('picker.file_failed'));
    return Array.isArray(r.paths) ? r.paths : null;
  }
  return openServerPicker({ initialDir, mode: 'file', kind, multiple: true, scope });
}

export function applyPickToInput(inputEl, path) {
  if (!inputEl || path == null || path === '') return false;
  inputEl.value = path;
  inputEl.dispatchEvent(new Event('input', { bubbles: true }));
  inputEl.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}

export function setBrowseButtonsVisible(root = document) {
  const desktop = isDesktop();
  root.querySelectorAll('.browse-btn, [data-desktop-browse]').forEach((btn) => {
    btn.style.display = '';
    btn.title = desktop ? t('picker.system_dialog') : t('picker.server_browse');
  });
}

export function initDesktopPickers(root = document) {
  setBrowseButtonsVisible(root);
  window.addEventListener('pywebviewready', () => setBrowseButtonsVisible(root));
}
