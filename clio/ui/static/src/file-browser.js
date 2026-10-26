import { api, icon } from './api.js';
import { addToast } from './toast.js';
import { onLocaleChange, t } from './i18n.js';

const NETWORK_ROOT = 'network://';
const NETWORK_MANUAL = 'network-manual://';

let activePicker = null;
let pickerRoot = null;

export function pickerTitle({ mode = 'file', multiple = false, kind = 'any' } = {}) {
  if (mode === 'folder') return t('file_browser.title_folder');
  if (multiple && kind === 'video') return t('file_browser.title_video');
  if (multiple) return t('file_browser.title_file');
  if (kind === 'exe') return t('file_browser.title_exe');
  return t('file_browser.title_file');
}

export function formatFileSize(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value < 0) return '';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function ensurePicker() {
  if (pickerRoot?.isConnected) return pickerRoot;
  pickerRoot = document.createElement('div');
  pickerRoot.id = 'server-file-picker';
  pickerRoot.className = 'modal fs-picker-modal';
  pickerRoot.style.display = 'none';
  pickerRoot.innerHTML = `
    <div class="modal-backdrop" data-fs-action="cancel"></div>
    <div class="modal-dialog fs-picker-dialog" role="dialog" aria-modal="true" aria-labelledby="fs-picker-title">
      <div class="fs-picker-header">
        <h3 id="fs-picker-title">${t('file_browser.dialog_title')}</h3>
        <button type="button" class="fs-picker-icon-btn" data-fs-action="cancel" title="${t('common.close')}" aria-label="${t('common.close')}" data-i18n-title="common.close" data-i18n-aria-label="common.close">×</button>
      </div>
      <div class="fs-picker-pathbar">
        <button type="button" class="fs-picker-icon-btn" data-fs-action="up" title="${t('file_browser.up')}" aria-label="${t('file_browser.up')}" data-i18n-title="file_browser.up" data-i18n-aria-label="file_browser.up">↑</button>
        <input id="fs-picker-path" type="text" aria-label="${t('file_browser.current_path')}" data-i18n-aria-label="file_browser.current_path">
        <button type="button" class="btn-secondary" data-fs-action="go" data-i18n="file_browser.go">${t('file_browser.go')}</button>
        <button type="button" class="fs-picker-icon-btn" data-fs-action="network" title="${t('file_browser.network_location')}" aria-label="${t('file_browser.network_location')}" data-i18n-title="file_browser.network_location" data-i18n-aria-label="file_browser.network_location">${icon('network', 16)}</button>
        <button type="button" class="fs-picker-icon-btn" data-fs-action="refresh" title="${t('common.refresh')}" aria-label="${t('common.refresh')}" data-i18n-title="common.refresh" data-i18n-aria-label="common.refresh">↻</button>
      </div>
      <div class="fs-picker-filterbar">
        <input id="fs-picker-filter" type="search" placeholder="${t('file_browser.filter_placeholder')}" aria-label="${t('file_browser.filter_aria')}" data-i18n-placeholder="file_browser.filter_placeholder" data-i18n-aria-label="file_browser.filter_aria">
        <button type="button" class="btn-secondary" data-fs-action="select-all" hidden>${t('file_browser.select_all')}</button>
      </div>
      <div id="fs-picker-status" class="fs-picker-status" aria-live="polite"></div>
      <div id="fs-picker-entries" class="fs-picker-entries" role="listbox" tabindex="0"></div>
      <div class="fs-picker-footer">
        <button type="button" class="btn-secondary" data-fs-action="mkdir" data-i18n="file_browser.mkdir">${t('file_browser.mkdir')}</button>
        <span id="fs-picker-selection" class="muted"></span>
        <button type="button" class="btn-secondary" data-fs-action="cancel" data-i18n="common.cancel">${t('common.cancel')}</button>
        <button type="button" class="btn-primary" data-fs-action="confirm" disabled>${t('file_browser.confirm')}</button>
      </div>
    </div>`;
  document.body.appendChild(pickerRoot);

  pickerRoot.addEventListener('click', (event) => {
    const action = event.target.closest('[data-fs-action]')?.dataset.fsAction;
    if (!action || !activePicker) return;
    if (action === 'cancel') finishPicker(null);
    if (action === 'up') goUp();
    if (action === 'go') navigateInput(pathInput().value.trim());
    if (action === 'network') loadNetworkServers();
    if (action === 'refresh') refreshCurrentView();
    if (action === 'confirm') confirmPicker();
    if (action === 'mkdir') createDirectory();
    if (action === 'select-all') toggleSelectAll();
  });
  pathInput().addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || !activePicker) return;
    event.preventDefault();
    navigateInput(pathInput().value.trim());
  });
  filterInput().addEventListener('input', () => {
    if (!activePicker) return;
    activePicker.highlight = -1;
    renderEntries(activePicker.lastData || { dirs: [], files: [] });
    updateFooter();
  });
  const handleListKeys = (event) => {
    if (!activePicker) return;
    if (event.key === 'ArrowDown') { event.preventDefault(); moveHighlight(1); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); moveHighlight(-1); }
    else if (event.key === 'Home') { event.preventDefault(); setHighlight(0); }
    else if (event.key === 'End') { event.preventDefault(); setHighlight(visibleEntries().length - 1); }
    else if (event.key === 'Enter') { event.preventDefault(); activateHighlighted(); }
  };
  filterInput().addEventListener('keydown', handleListKeys);
  entriesList().addEventListener('keydown', handleListKeys);
  entriesList().addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
      event.preventDefault();
      toggleSelectAll();
    }
  });
  entriesList().addEventListener('focusin', (event) => {
    if (!activePicker) return;
    const entry = event.target?.closest?.('.fs-picker-entry');
    const index = entry ? visibleEntries().indexOf(entry) : -1;
    if (index >= 0) setHighlight(index);
  });
  onLocaleChange(() => {
    if (!activePicker) return;
    updatePickerTitle();
    updateFooter();
    renderEntries(activePicker.lastData || { dirs: [], files: [] });
  });
  return pickerRoot;
}

function pathInput() {
  return pickerRoot.querySelector('#fs-picker-path');
}

function filterInput() {
  return pickerRoot.querySelector('#fs-picker-filter');
}

function entriesList() {
  return pickerRoot.querySelector('#fs-picker-entries');
}

function filterQuery() {
  return (filterInput()?.value || '').trim().toLowerCase();
}

function visibleEntries() {
  return [...pickerRoot.querySelectorAll('.fs-picker-entry')];
}

function visibleFileEntries() {
  if (!activePicker) return [];
  const query = filterQuery();
  return (activePicker.lastData?.files || []).filter((entry) => {
    if (!query) return true;
    return String(entry.name || entry.path || '').toLowerCase().includes(query);
  });
}

function updatePickerTitle() {
  if (!activePicker) return;
  const title = activePicker.view === 'fs' ? pickerTitle(activePicker) : t('file_browser.network_title');
  pickerRoot.querySelector('#fs-picker-title').textContent = title;
}

function updateUpButton() {
  if (!activePicker) return;
  const up = pickerRoot.querySelector('[data-fs-action="up"]');
  up.disabled = activePicker.view === 'fs' ? activePicker.parent == null : false;
}

function parseNetworkInput(value) {
  const normalized = String(value || '').trim().replace(/\//g, '\\');
  if (!normalized.startsWith('\\\\')) return null;
  const parts = normalized.slice(2).split('\\').filter(Boolean);
  if (!parts.length) return null;
  const server = `\\\\${parts[0]}`;
  const rest = parts.slice(1).join('\\');
  return { server, path: rest ? `${server}\\${rest}` : null };
}

function navigateInput(value) {
  if (!activePicker) return;
  if (!String(value || '').trim()) {
    refreshCurrentView();
    return;
  }
  const parsed = parseNetworkInput(value);
  if (parsed && !parsed.path) {
    void loadNetworkShares(parsed.server);
    return;
  }
  if (activePicker.view !== 'fs') {
    if (!parsed) {
      addToast(t('file_browser.network_invalid'), 'warning');
      return;
    }
    void loadPath(parsed.path, false);
    return;
  }
  void loadPath(value, false);
}

function goUp() {
  if (!activePicker) return;
  if (activePicker.view === 'shares') {
    void loadNetworkServers();
    return;
  }
  if (activePicker.view === 'servers') {
    void loadPath('', false);
    return;
  }
  if (activePicker.parent != null) void loadPath(activePicker.parent, false);
}

function refreshCurrentView() {
  if (!activePicker) return;
  if (activePicker.view === 'servers') {
    void loadNetworkServers();
    return;
  }
  if (activePicker.view === 'shares') {
    void loadNetworkShares(activePicker.networkServer);
    return;
  }
  void loadPath(activePicker.currentPath, false);
}

function toggleSelectAll() {
  if (!activePicker || !activePicker.multiple || activePicker.view !== 'fs') return;
  const files = visibleFileEntries();
  if (!files.length) return;
  const allSelected = files.every((file) => activePicker.selected.has(file.path));
  for (const file of files) {
    if (allSelected) activePicker.selected.delete(file.path);
    else activePicker.selected.add(file.path);
  }
  renderEntries(activePicker.lastData || { dirs: [], files: [] });
  updateFooter();
}

function setHighlight(index) {
  const items = visibleEntries();
  if (!activePicker) return;
  if (!items.length) {
    activePicker.highlight = -1;
    entriesList()?.removeAttribute('aria-activedescendant');
    return;
  }
  const clamped = Math.max(0, Math.min(items.length - 1, index));
  items.forEach((el, i) => el.classList.toggle('active', i === clamped));
  activePicker.highlight = clamped;
  items[clamped].scrollIntoView?.({ block: 'nearest' });
  entriesList()?.setAttribute('aria-activedescendant', items[clamped].id);
}

function moveHighlight(delta) {
  if (!activePicker) return;
  const items = visibleEntries();
  if (!items.length) return;
  const cur = activePicker.highlight ?? -1;
  setHighlight(cur < 0 ? (delta > 0 ? 0 : items.length - 1) : cur + delta);
}

function activateHighlighted() {
  if (!activePicker) return;
  const items = visibleEntries();
  if (!items.length) return;
  const index = activePicker.highlight ?? -1;
  const el = items[index >= 0 && index < items.length ? index : 0];
  const isDir = el.classList.contains('fs-picker-dir');
  el.click();
  if (!isDir && !activePicker.multiple) confirmPicker();
}

function finishPicker(value, error = null) {
  const picker = activePicker;
  if (!picker) return;
  activePicker = null;
  pickerRoot.style.display = 'none';
  document.removeEventListener('keydown', picker.keyHandler);
  if (error) picker.reject(error);
  else picker.resolve(value);
}

function confirmPicker() {
  if (!activePicker) return;
  if (activePicker.mode === 'folder') {
    if (activePicker.currentPath) finishPicker(activePicker.currentPath);
    return;
  }
  const selected = [...activePicker.selected];
  if (!selected.length) return;
  finishPicker(activePicker.multiple ? selected : selected[0]);
}

function updateFooter() {
  if (!activePicker) return;
  const confirm = pickerRoot.querySelector('[data-fs-action="confirm"]');
  const selection = pickerRoot.querySelector('#fs-picker-selection');
  const selectAll = pickerRoot.querySelector('[data-fs-action="select-all"]');
  const showSelectAll = activePicker.multiple && activePicker.view === 'fs';
  if (selectAll) {
    selectAll.hidden = !showSelectAll;
    if (showSelectAll) {
      const files = visibleFileEntries();
      const allSelected = files.length > 0 && files.every((file) => activePicker.selected.has(file.path));
      selectAll.textContent = allSelected ? t('file_browser.deselect_all') : t('file_browser.select_all');
      selectAll.disabled = files.length === 0;
    }
  }
  if (activePicker.mode === 'folder') {
    confirm.disabled = activePicker.view !== 'fs' || !activePicker.currentPath;
    confirm.textContent = t('file_browser.pick_current_dir');
    selection.textContent = activePicker.currentPath ? t('file_browser.current_dir') : '';
    return;
  }
  const count = activePicker.selected.size;
  confirm.disabled = count === 0;
  confirm.textContent = activePicker.multiple ? t('file_browser.pick_count', { count: count || '' }) : t('file_browser.title_file');
  selection.textContent = count ? t('file_browser.selected_count', { count }) : '';
}

function entryButton(entry, type) {
  const button = document.createElement('button');
  button.type = 'button';
  button.tabIndex = -1;
  button.className = `fs-picker-entry fs-picker-${type}`;
  button.dataset.path = entry.path;
  button.setAttribute('role', 'option');
  button.innerHTML = icon(entry.icon || (type === 'dir' ? 'folder' : 'file'), 18);
  const name = document.createElement('span');
  name.className = 'fs-picker-entry-name';
  name.textContent = entry.name || entry.path;
  button.appendChild(name);
  const detail = type === 'file' ? formatFileSize(entry.size) : (entry.comment || '');
  if (detail) {
    const size = document.createElement('span');
    size.className = 'fs-picker-entry-size';
    size.textContent = detail;
    button.appendChild(size);
  }
  return button;
}

function renderEntries(data) {
  if (!activePicker) return;
  activePicker.lastData = data;
  const list = entriesList();
  list.innerHTML = '';
  list.removeAttribute('aria-activedescendant');
  const query = filterQuery();
  const matches = (entry) => !query || String(entry.name || entry.path || '').toLowerCase().includes(query);
  const dirs = (data.dirs || []).filter(matches);
  const files = (data.files || []).filter(matches);
  if (activePicker.view === 'fs' && data.is_drive_list) {
    dirs.push({ name: t('file_browser.network_location'), path: NETWORK_ROOT, icon: 'network', isNetwork: true });
  }
  if (activePicker.view === 'servers') {
    dirs.push({ name: t('file_browser.network_add_server'), path: NETWORK_MANUAL, icon: 'plus', isManual: true });
  }
  let optionIndex = 0;
  const assignId = (button) => { button.id = `fs-picker-opt-${optionIndex++}`; };
  for (const dir of dirs) {
    const button = entryButton(dir, 'dir');
    assignId(button);
    button.addEventListener('click', () => {
      if (dir.isNetwork) {
        void loadNetworkServers();
        return;
      }
      if (dir.isManual) {
        promptNetworkServer();
        return;
      }
      if (activePicker?.view === 'servers') {
        void loadNetworkShares(dir.path);
        return;
      }
      void loadPath(dir.path, false);
    });
    list.appendChild(button);
  }
  if (activePicker.mode !== 'folder' && activePicker.view === 'fs') {
    for (const file of files) {
      const button = entryButton(file, 'file');
      assignId(button);
      const selected = activePicker.selected.has(file.path);
      button.classList.toggle('selected', selected);
      button.setAttribute('aria-selected', String(selected));
      button.addEventListener('click', () => {
        if (!activePicker) return;
        if (activePicker.multiple) {
          if (activePicker.selected.has(file.path)) activePicker.selected.delete(file.path);
          else activePicker.selected.add(file.path);
        } else {
          activePicker.selected.clear();
          activePicker.selected.add(file.path);
          list.querySelectorAll('.fs-picker-file.selected').forEach(el => el.classList.remove('selected'));
        }
        button.classList.toggle('selected', activePicker.selected.has(file.path));
        button.setAttribute('aria-selected', String(activePicker.selected.has(file.path)));
        updateFooter();
      });
      button.addEventListener('dblclick', () => {
        if (!activePicker) return;
        if (activePicker.multiple) {
          activePicker.selected.add(file.path);
          button.classList.add('selected');
          button.setAttribute('aria-selected', 'true');
          updateFooter();
        } else {
          activePicker.selected.clear();
          activePicker.selected.add(file.path);
        }
        confirmPicker();
      });
      list.appendChild(button);
    }
  }
  if (!list.children.length) {
    const empty = document.createElement('p');
    empty.className = 'fs-picker-empty muted';
    if (query) empty.textContent = t('file_browser.no_filter_match');
    else if (activePicker.view === 'servers') empty.textContent = t('file_browser.network_empty');
    else if (activePicker.view === 'shares') empty.textContent = t('file_browser.network_shares_empty');
    else empty.textContent = activePicker.mode === 'folder' ? t('file_browser.empty_dirs') : t('file_browser.empty_files');
    list.appendChild(empty);
  }
  activePicker.highlight = -1;
}

async function loadPath(path, fallbackToRoot) {
  if (!activePicker) return;
  const picker = activePicker;
  const requestId = ++picker.requestId;
  if (picker.view !== 'fs') {
    picker.view = 'fs';
    picker.networkServer = null;
    updatePickerTitle();
  }
  const status = pickerRoot.querySelector('#fs-picker-status');
  const entries = pickerRoot.querySelector('#fs-picker-entries');
  status.textContent = t('file_browser.reading');
  status.className = 'fs-picker-status muted';
  entries.setAttribute('aria-busy', 'true');
  try {
    const query = `/api/fs/entries?path=${encodeURIComponent(path || '')}&kind=${encodeURIComponent(picker.kind)}&scope=${encodeURIComponent(picker.scope)}`;
    const data = await api('GET', query);
    if (activePicker !== picker || requestId !== picker.requestId) return;
    picker.currentPath = data.path || '';
    picker.parent = data.parent ?? null;
    pathInput().value = picker.currentPath;
    updateUpButton();
    pickerRoot.querySelector('[data-fs-action="mkdir"]').disabled = !picker.currentPath;
    if (picker.mode !== 'folder' && data.selected_path) {
      if (!picker.multiple) picker.selected.clear();
      picker.selected.add(data.selected_path);
    }
    status.textContent = data.is_drive_list
      ? t('file_browser.drive_list')
      : t('file_browser.dir_count', { count: (data.dirs || []).length })
        + (picker.mode === 'folder' ? '' : t('file_browser.file_count', { count: (data.files || []).length }));
    renderEntries(data);
    updateFooter();
  } catch (error) {
    if (activePicker !== picker || requestId !== picker.requestId) return;
    if (fallbackToRoot && path) {
      await loadPath('', false);
      if (activePicker === picker) {
        status.textContent = t('file_browser.initial_unavailable', { detail: error.message || error });
        status.className = 'fs-picker-status warn';
        addToast(status.textContent, 'warning');
      }
      return;
    }
    status.textContent = t('file_browser.open_failed', { detail: error.message || error });
    status.className = 'fs-picker-status err';
    addToast(status.textContent, 'error', 6000);
  } finally {
    if (activePicker === picker && requestId === picker.requestId) entries.removeAttribute('aria-busy');
  }
}

async function loadNetworkServers() {
  if (!activePicker) return;
  const picker = activePicker;
  const requestId = ++picker.requestId;
  picker.view = 'servers';
  picker.networkServer = null;
  picker.currentPath = '';
  picker.parent = '';
  const status = pickerRoot.querySelector('#fs-picker-status');
  const entries = pickerRoot.querySelector('#fs-picker-entries');
  status.textContent = t('file_browser.reading');
  status.className = 'fs-picker-status muted';
  entries.setAttribute('aria-busy', 'true');
  pathInput().value = '';
  updatePickerTitle();
  updateUpButton();
  pickerRoot.querySelector('[data-fs-action="mkdir"]').disabled = true;
  try {
    const data = await api('GET', '/api/fs/network');
    if (activePicker !== picker || requestId !== picker.requestId) return;
    const servers = Array.isArray(data.servers) ? data.servers : [];
    status.textContent = servers.length
      ? t('file_browser.network_servers', { count: servers.length })
      : t('file_browser.network_empty');
    status.className = 'fs-picker-status muted';
    renderEntries({
      dirs: servers.map((server) => ({ ...server, icon: 'network' })),
      files: [],
    });
    updateFooter();
  } catch (error) {
    if (activePicker !== picker || requestId !== picker.requestId) return;
    status.textContent = t('file_browser.open_failed', { detail: error.message || error });
    status.className = 'fs-picker-status err';
    addToast(status.textContent, 'error', 6000);
  } finally {
    if (activePicker === picker && requestId === picker.requestId) entries.removeAttribute('aria-busy');
  }
}

async function loadNetworkShares(server) {
  if (!activePicker) return;
  const picker = activePicker;
  const requestId = ++picker.requestId;
  picker.view = 'shares';
  picker.networkServer = server;
  picker.currentPath = server;
  picker.parent = '';
  const status = pickerRoot.querySelector('#fs-picker-status');
  const entries = pickerRoot.querySelector('#fs-picker-entries');
  status.textContent = t('file_browser.reading');
  status.className = 'fs-picker-status muted';
  entries.setAttribute('aria-busy', 'true');
  pathInput().value = server;
  updatePickerTitle();
  updateUpButton();
  pickerRoot.querySelector('[data-fs-action="mkdir"]').disabled = true;
  try {
    const data = await api('GET', `/api/fs/network?server=${encodeURIComponent(server)}`);
    if (activePicker !== picker || requestId !== picker.requestId) return;
    const shares = Array.isArray(data.shares) ? data.shares : [];
    status.textContent = shares.length
      ? t('file_browser.network_shares', { count: shares.length })
      : t('file_browser.network_shares_empty');
    status.className = 'fs-picker-status muted';
    renderEntries({ dirs: shares, files: [] });
    updateFooter();
  } catch (error) {
    if (activePicker !== picker || requestId !== picker.requestId) return;
    status.textContent = t('file_browser.open_failed', { detail: error.message || error });
    status.className = 'fs-picker-status err';
    addToast(status.textContent, 'error', 6000);
  } finally {
    if (activePicker === picker && requestId === picker.requestId) entries.removeAttribute('aria-busy');
  }
}

function promptNetworkServer() {
  if (!activePicker) return;
  const value = window.prompt(t('file_browser.network_prompt'), activePicker.networkServer || '\\\\');
  if (value == null) return;
  const parsed = parseNetworkInput(value);
  if (!parsed) {
    addToast(t('file_browser.network_invalid'), 'warning');
    return;
  }
  if (parsed.path) void loadPath(parsed.path, false);
  else void loadNetworkShares(parsed.server);
}

async function createDirectory() {
  const picker = activePicker;
  if (!picker?.currentPath) return;
  const name = prompt(t('file_browser.new_folder_prompt'));
  if (!name?.trim()) return;
  const status = pickerRoot.querySelector('#fs-picker-status');
  try {
    const result = await api('POST', '/api/fs/mkdir', {
      parent: picker.currentPath,
      name: name.trim(),
    });
    if (activePicker !== picker) return;
    await loadPath(result.path || picker.currentPath, false);
  } catch (error) {
    if (activePicker !== picker) return;
    status.textContent = t('file_browser.mkdir_failed', { detail: error.message || error });
    status.className = 'fs-picker-status err';
    addToast(status.textContent, 'error', 6000);
  }
}

export function openServerPicker({
  initialDir = '',
  mode = 'file',
  kind = 'any',
  multiple = false,
  scope = 'project',
} = {}) {
  ensurePicker();
  if (activePicker) finishPicker(null);
  return new Promise((resolve, reject) => {
    const keyHandler = (event) => {
      if (event.key === 'Escape') finishPicker(null);
    };
    activePicker = {
      resolve,
      reject,
      mode,
      kind,
      multiple,
      scope,
      selected: new Set(),
      currentPath: '',
      parent: null,
      view: 'fs',
      networkServer: null,
      requestId: 0,
      keyHandler,
    };
    activePicker.lastData = { dirs: [], files: [] };
    activePicker.highlight = -1;
    filterInput().value = '';
    updatePickerTitle();
    pickerRoot.style.display = 'flex';
    document.addEventListener('keydown', keyHandler);
    updateFooter();
    void loadPath(initialDir, true);
  });
}
