import { state } from './state.js';

function $(id) {
  return document.getElementById(id);
}

function $$(sel) {
  return document.querySelectorAll(sel);
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function fmtTime(sec) {
  if (!Number.isFinite(sec)) return '00:00';
  const m = Math.floor(sec / 60).toString().padStart(2, '0');
  const s = Math.floor(sec % 60).toString().padStart(2, '0');
  return `${m}:${s}`;
}

function parseTimecode(s) {
  /**
   * Parse a timecode to seconds.
   * Accepts plain seconds (`12`, `0.12`) or `MM:SS` / `HH:MM:SS` with optional
   * fractional seconds on the last field. Dots are never treated as separators
   * (so `0.12` stays 0.12s, not `0:12` → 12s) — P2-P43.
   * Invalid strings return NaN (except null/empty → 0 for legacy timeline callers).
   */
  if (s == null || String(s).trim() === '') return 0;
  const str = String(s).trim();
  if (/^\d+(\.\d+)?$/.test(str)) {
    const n = Number(str);
    return Number.isFinite(n) ? n : NaN;
  }
  const three = str.match(/^(\d+):([0-5]?\d):([0-5]?\d(?:\.\d+)?)$/);
  if (three) {
    const h = Number(three[1]);
    const min = Number(three[2]);
    const sec = Number(three[3]);
    if (![h, min, sec].every(Number.isFinite) || min >= 60 || sec >= 60) return NaN;
    return h * 3600 + min * 60 + sec;
  }
  const two = str.match(/^(\d+):([0-5]?\d(?:\.\d+)?)$/);
  if (two) {
    const min = Number(two[1]);
    const sec = Number(two[2]);
    if (![min, sec].every(Number.isFinite) || sec >= 60) return NaN;
    return min * 60 + sec;
  }
  return NaN;
}

function getDeep(obj, path) {
  return String(path).split('.').reduce((o, k) => (o != null ? o[k] : undefined), obj);
}

function setDeep(obj, path, value) {
  const keys = String(path).split('.');
  let cur = obj;
  for (let i = 0; i < keys.length - 1; i++) {
    if (!cur[keys[i]] || typeof cur[keys[i]] !== 'object') cur[keys[i]] = {};
    cur = cur[keys[i]];
  }
  cur[keys[keys.length - 1]] = value;
}

function markDirty() { state.dirty = true; updateSaveBtn(); }

/** Clear unsaved flag and sync the Save button label (discard / after save). */
function clearDirty() { state.dirty = false; updateSaveBtn(); }

function updateSaveBtn() {
  const btn = $('btn-save');
  if (!btn) return;
  btn.classList.toggle('dirty', state.dirty);
  btn.textContent = state.dirty ? '保存 (有改动)' : '保存';
}

function setStatus(msg, kind = '') {
  const el = $('status');
  if (!el) return;
  el.textContent = msg || '';
  el.className = 'status ' + kind;
  if (msg) {
    const captured = msg;
    // Errors stick longer so users can read them; info/ok clear quickly
    const ttl = kind === 'err' || kind === 'error' ? 8000 : 4000;
    setTimeout(() => { if (el.textContent === captured) el.textContent = ''; }, ttl);
  }
}

function updateSidebarDay() {
  const el = document.querySelector('.project-item[data-entity="plan"] .muted');
  if (el) el.textContent = state.currentDay;
}

function updateProjectSidebar() {
  const nameEl = $('proj-name');
  const switcher = $('btn-project-switcher');
  const pathEl = $('project-menu-path');
  const revealBtn = $('btn-reveal-project');
  const name = state.currentProject?.name || state.currentProjectName || state.projectName || '选择项目';
  const path = state.currentProjectDir || state.currentProject?.project_dir || state.config?.project_dir || '';
  if (nameEl) {
    nameEl.textContent = name;
    nameEl.title = path ? `${name}\n${path}` : name;
  }
  if (switcher) {
    switcher.title = path ? `当前项目: ${name}\n${path}` : '选择项目或管理当前项目';
    switcher.classList.toggle('is-empty', !path);
  }
  if (pathEl) pathEl.textContent = path ? `${name} · ${path}` : '尚未打开项目';
  if (revealBtn) {
    revealBtn.disabled = !path;
    revealBtn.setAttribute('aria-disabled', String(!path));
  }
}

function updateEntityUI() {
  const saveBtn = $('btn-save');
  if (saveBtn) saveBtn.style.display = '';
  const cls = state.currentEntity === 'plan' ? 'entity-plan'
    : state.currentEntity === 'run' ? 'entity-run'
    : state.currentEntity === 'config' ? 'entity-config'
    : state.currentEntity === 'logs' ? 'entity-logs'
    : state.currentEntity === 'tokens' ? 'entity-tokens'
    : state.currentEntity === 'tasks' ? 'entity-tasks'
    : 'entity-video';
  $('editor').className = cls;
  const previewBar = $('preview-bar');
  if (previewBar) previewBar.style.display = state.currentEntity === 'plan' ? 'flex' : 'none';
  $$('.project-item').forEach(p => p.classList.remove('active'));
  if (state.currentEntity === 'plan') {
    document.querySelector('.project-item[data-entity="plan"]').classList.add('active');
    $$('.video-item').forEach(v => v.classList.remove('active'));
  } else if (state.currentEntity === 'run') {
    document.querySelector('.project-item[data-entity="run"]').classList.add('active');
    $$('.video-item').forEach(v => v.classList.remove('active'));
  } else if (state.currentEntity === 'config') {
    document.querySelector('.project-item[data-entity="config"]').classList.add('active');
    $$('.video-item').forEach(v => v.classList.remove('active'));
  } else if (state.currentEntity === 'logs') {
    document.querySelector('.project-item[data-entity="logs"]').classList.add('active');
    $$('.video-item').forEach(v => v.classList.remove('active'));
  } else if (state.currentEntity === 'tokens') {
    document.querySelector('.project-item[data-entity="tokens"]').classList.add('active');
    $$('.video-item').forEach(v => v.classList.remove('active'));
  } else if (state.currentEntity === 'tasks') {
    document.querySelector('.project-item[data-entity="tasks"]')?.classList.add('active');
    $$('.video-item').forEach(v => v.classList.remove('active'));
  }
}

export {
  $, $$,
  escapeHtml,
  fmtTime,
  parseTimecode,
  getDeep,
  setDeep,
  markDirty,
  clearDirty,
  updateSaveBtn,
  setStatus,
  updateSidebarDay,
  updateProjectSidebar,
  updateEntityUI,
};
