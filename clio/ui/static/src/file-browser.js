import { api, icon } from './api.js';

let activePicker = null;
let pickerRoot = null;

export function pickerTitle({ mode = 'file', multiple = false, kind = 'any' } = {}) {
  if (mode === 'folder') return '选择目录';
  if (multiple && kind === 'video') return '选择视频';
  if (multiple) return '选择文件';
  if (kind === 'exe') return '选择可执行文件';
  return '选择文件';
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
        <h3 id="fs-picker-title">选择文件</h3>
        <button type="button" class="fs-picker-icon-btn" data-fs-action="cancel" title="关闭" aria-label="关闭">×</button>
      </div>
      <div class="fs-picker-pathbar">
        <button type="button" class="fs-picker-icon-btn" data-fs-action="up" title="上一级" aria-label="上一级">↑</button>
        <input id="fs-picker-path" type="text" aria-label="当前路径">
        <button type="button" class="btn-secondary" data-fs-action="go">转到</button>
        <button type="button" class="fs-picker-icon-btn" data-fs-action="refresh" title="刷新" aria-label="刷新">↻</button>
      </div>
      <div id="fs-picker-status" class="fs-picker-status" aria-live="polite"></div>
      <div id="fs-picker-entries" class="fs-picker-entries" role="listbox"></div>
      <div class="fs-picker-footer">
        <button type="button" class="btn-secondary" data-fs-action="mkdir">新建文件夹</button>
        <span id="fs-picker-selection" class="muted"></span>
        <button type="button" class="btn-secondary" data-fs-action="cancel">取消</button>
        <button type="button" class="btn-primary" data-fs-action="confirm" disabled>选择</button>
      </div>
    </div>`;
  document.body.appendChild(pickerRoot);

  pickerRoot.addEventListener('click', (event) => {
    const action = event.target.closest('[data-fs-action]')?.dataset.fsAction;
    if (!action || !activePicker) return;
    if (action === 'cancel') finishPicker(null);
    if (action === 'up' && activePicker.parent != null) loadPath(activePicker.parent, false);
    if (action === 'go') loadPath(pathInput().value.trim(), false);
    if (action === 'refresh') loadPath(activePicker.currentPath, false);
    if (action === 'confirm') confirmPicker();
    if (action === 'mkdir') createDirectory();
  });
  pathInput().addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || !activePicker) return;
    event.preventDefault();
    loadPath(pathInput().value.trim(), false);
  });
  return pickerRoot;
}

function pathInput() {
  return pickerRoot.querySelector('#fs-picker-path');
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
  if (activePicker.mode === 'folder') {
    confirm.disabled = !activePicker.currentPath;
    confirm.textContent = '选择当前目录';
    selection.textContent = activePicker.currentPath ? '当前目录' : '';
    return;
  }
  const count = activePicker.selected.size;
  confirm.disabled = count === 0;
  confirm.textContent = activePicker.multiple ? `选择 ${count || ''} 个文件`.replace('  ', ' ') : '选择文件';
  selection.textContent = count ? `已选 ${count} 个` : '';
}

function entryButton(entry, type) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `fs-picker-entry fs-picker-${type}`;
  button.dataset.path = entry.path;
  button.setAttribute('role', 'option');
  button.innerHTML = icon(type === 'dir' ? 'folder' : 'file', 18);
  const name = document.createElement('span');
  name.className = 'fs-picker-entry-name';
  name.textContent = entry.name || entry.path;
  button.appendChild(name);
  if (type === 'file') {
    const size = document.createElement('span');
    size.className = 'fs-picker-entry-size';
    size.textContent = formatFileSize(entry.size);
    button.appendChild(size);
  }
  return button;
}

function renderEntries(data) {
  if (!activePicker) return;
  const list = pickerRoot.querySelector('#fs-picker-entries');
  list.innerHTML = '';
  for (const dir of data.dirs || []) {
    const button = entryButton(dir, 'dir');
    button.addEventListener('click', () => loadPath(dir.path, false));
    list.appendChild(button);
  }
  if (activePicker.mode !== 'folder') {
    for (const file of data.files || []) {
      const button = entryButton(file, 'file');
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
        if (!activePicker?.multiple) {
          activePicker.selected.clear();
          activePicker.selected.add(file.path);
          confirmPicker();
        }
      });
      list.appendChild(button);
    }
  }
  if (!list.children.length) {
    const empty = document.createElement('p');
    empty.className = 'fs-picker-empty muted';
    empty.textContent = activePicker.mode === 'folder' ? '此目录没有子目录' : '此目录没有符合条件的文件';
    list.appendChild(empty);
  }
}

async function loadPath(path, fallbackToRoot) {
  if (!activePicker) return;
  const picker = activePicker;
  const requestId = ++picker.requestId;
  const status = pickerRoot.querySelector('#fs-picker-status');
  const entries = pickerRoot.querySelector('#fs-picker-entries');
  status.textContent = '正在读取...';
  status.className = 'fs-picker-status muted';
  entries.setAttribute('aria-busy', 'true');
  try {
    const query = `/api/fs/entries?path=${encodeURIComponent(path || '')}&kind=${encodeURIComponent(picker.kind)}&scope=${encodeURIComponent(picker.scope)}`;
    const data = await api('GET', query);
    if (activePicker !== picker || requestId !== picker.requestId) return;
    picker.currentPath = data.path || '';
    picker.parent = data.parent ?? null;
    pathInput().value = picker.currentPath;
    pickerRoot.querySelector('[data-fs-action="up"]').disabled = picker.parent == null;
    pickerRoot.querySelector('[data-fs-action="mkdir"]').disabled = !picker.currentPath;
    if (picker.mode !== 'folder' && data.selected_path) {
      if (!picker.multiple) picker.selected.clear();
      picker.selected.add(data.selected_path);
    }
    status.textContent = data.is_drive_list ? '选择磁盘' : `${(data.dirs || []).length} 个目录${picker.mode === 'folder' ? '' : `，${(data.files || []).length} 个文件`}`;
    renderEntries(data);
    updateFooter();
  } catch (error) {
    if (activePicker !== picker || requestId !== picker.requestId) return;
    if (fallbackToRoot && path) {
      await loadPath('', false);
      if (activePicker === picker) {
        status.textContent = `初始路径不可用，已显示可浏览位置：${error.message || error}`;
        status.className = 'fs-picker-status warn';
      }
      return;
    }
    status.textContent = `无法打开此路径：${error.message || error}`;
    status.className = 'fs-picker-status err';
  } finally {
    if (activePicker === picker && requestId === picker.requestId) entries.removeAttribute('aria-busy');
  }
}

async function createDirectory() {
  if (!activePicker?.currentPath) return;
  const name = prompt('新文件夹名称');
  if (!name?.trim()) return;
  const status = pickerRoot.querySelector('#fs-picker-status');
  try {
    const result = await api('POST', '/api/fs/mkdir', {
      parent: activePicker.currentPath,
      name: name.trim(),
    });
    await loadPath(result.path || activePicker.currentPath, false);
  } catch (error) {
    status.textContent = `创建失败：${error.message || error}`;
    status.className = 'fs-picker-status err';
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
      requestId: 0,
      keyHandler,
    };
    pickerRoot.querySelector('#fs-picker-title').textContent = pickerTitle({ mode, multiple, kind });
    pickerRoot.style.display = 'flex';
    document.addEventListener('keydown', keyHandler);
    updateFooter();
    void loadPath(initialDir, true);
  });
}
