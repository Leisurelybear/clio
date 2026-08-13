import { $ } from './utils.js';

const STORAGE_KEY = 'vlog_ui_layout';
const SIDEBAR_MIN = 160;
const SIDEBAR_MAX = 400;
const EDITOR_MIN = 280;
const EDITOR_MAX = 600;

function initLayout() {
  const saved = loadLayout();
  const root = document.documentElement;

  if (saved.sidebarW && saved.sidebarW >= SIDEBAR_MIN && saved.sidebarW <= SIDEBAR_MAX) {
    root.style.setProperty('--sidebar-w', saved.sidebarW + 'px');
  }
  if (saved.editorW && saved.editorW >= EDITOR_MIN && saved.editorW <= EDITOR_MAX) {
    root.style.setProperty('--editor-w', saved.editorW + 'px');
  }

  if (saved.sidebarCollapsed) document.body.classList.add('sidebar-collapsed');
  if (saved.editorCollapsed) document.body.classList.add('editor-collapsed');

  setupResizeHandles();
  setupCollapseButtons();
  setupKeyboardShortcuts();
}

function setupCollapseButtons() {
  $('btn-collapse-sidebar')?.addEventListener('click', () => {
    document.body.classList.toggle('sidebar-collapsed');
    saveLayout();
  });
  $('btn-collapse-editor')?.addEventListener('click', () => {
    document.body.classList.toggle('editor-collapsed');
    saveLayout();
  });
}

// ── Resize Handles (drag + click to collapse) ──────────────

function setupResizeHandles() {
  document.querySelectorAll('.resize-handle').forEach(handle => {
    handle.addEventListener('mousedown', onResizeStart);
    handle.addEventListener('click', onResizeClick);
  });
}

let _dragDistance = 0;

function _calculateResizeWidth(startWidth, startX, currentX, isSidebar, minWidth, maxWidth) {
  const displacement = currentX - startX;
  const width = isSidebar ? startWidth + displacement : startWidth - displacement;
  return Math.max(minWidth, Math.min(maxWidth, width));
}

function onResizeStart(e) {
  e.preventDefault();
  _dragDistance = 0;
  const handle = e.currentTarget;
  const side = handle.dataset.side;
  const root = document.documentElement;
  const startX = e.clientX;

  const isSidebar = side === 'sidebar';
  const prop = isSidebar ? '--sidebar-w' : '--editor-w';
  const minW = isSidebar ? SIDEBAR_MIN : EDITOR_MIN;
  const maxW = isSidebar ? SIDEBAR_MAX : EDITOR_MAX;
  const startWidth = parseFloat(root.style.getPropertyValue(prop))
    || parseFloat(getComputedStyle(root).getPropertyValue(prop))
    || (isSidebar ? 240 : 400);

  function onMove(ev) {
    _dragDistance = Math.max(_dragDistance, Math.abs(ev.clientX - startX));
    const newW = _calculateResizeWidth(startWidth, startX, ev.clientX, isSidebar, minW, maxW);
    root.style.setProperty(prop, newW + 'px');
  }

  function onUp() {
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    saveLayout();
  }

  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
  document.body.style.cursor = 'col-resize';
  document.body.style.userSelect = 'none';
}

function onResizeClick(e) {
  if (_dragDistance > 5) return;
  const side = e.currentTarget.dataset.side;
  document.body.classList.toggle(side === 'sidebar' ? 'sidebar-collapsed' : 'editor-collapsed');
  saveLayout();
}

function setupKeyboardShortcuts() {
  document.addEventListener('keydown', (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key === 'b') {
      e.preventDefault();
      document.body.classList.toggle('sidebar-collapsed');
      saveLayout();
    }
    if (mod && e.key === '\\') {
      e.preventDefault();
      document.body.classList.toggle('editor-collapsed');
      saveLayout();
    }
  });
}

// ── Layout Persistence ──────────────────────────────────────

function saveLayout() {
  const root = document.documentElement;
  const layout = {
    sidebarW: parseFloat(root.style.getPropertyValue('--sidebar-w')) || null,
    editorW: parseFloat(root.style.getPropertyValue('--editor-w')) || null,
    sidebarCollapsed: document.body.classList.contains('sidebar-collapsed'),
    editorCollapsed: document.body.classList.contains('editor-collapsed'),
  };

  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
  } catch { /* ignore */ }
}

function loadLayout() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

export { initLayout, _calculateResizeWidth };
