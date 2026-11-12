import { state } from './state.js';
import {
  $, $$, escapeHtml, setStatus, updateProjectSidebar,
} from './utils.js';
import { api, icon } from './api.js';
import { beginLatest, endLatest, isLatest, isAbortError } from './latest.js';
import { updateRunFilesBadge } from './runner.js';
import {
  buildVideoMenuItems,
  videoMenuItemsToHtml,
} from './video-menu.js';
import {
  buildVideoStageStatuses,
  chipDefsForSource,
  filterVideos,
  stageLabel,
  STAGE_CELLS,
} from './sidebar-video-filter.js';
import { toggleSelectionFor, videoSelectionKey } from './video-selection.js';
import { selectVideosButtonHtml } from './select-btn.js';
import { bindVideoInfoHover, clearVideoInfoCache } from './video-info.js';
import { t, onLocaleChange } from './i18n.js';
import { confirmDialog } from './confirm.js';
import { renderFilterChips, renderOfflineSummary, renderStageCountBar } from './sidebar-video-overview.js';
import { bindCoverThumbFallback, videoDisplayName, videoThumbHtml } from './sidebar-video-display.js';
import { renderVideoItem } from './sidebar-video-row.js';

// ── Video relink helper ───────────────────────────────────────

/** Open relink modal (type path or browse). API submit lives in sidebar-relink.js. */
export async function relinkVideo(file, absPath) {
  const oldPath = absPath || file;
  const { openRelinkModal } = await import('./sidebar-relink.js');
  openRelinkModal({ oldPath, displayName: file });
}

/** Programmatic relink used by tests and callers that already have a new path. */
export async function submitRelink(oldPath, newPath) {
  const r = await api('PUT', '/api/videos/relink', { old_path: oldPath, new_path: newPath });
  if (r.ok) {
    setStatus(t('sidebar.relinked', { path: newPath }), 'ok');
    await loadVideos();
    return r;
  }
  setStatus(t('sidebar.relink_failed', { detail: r.error || t('common.unknown_error') }), 'err');
  return r;
}

// ── Video removal helpers ─────────────────────────────────────

function _stripDisplay(file) {
  const display = String(file || '');
  const baseName = display.replace(/^.*[\\/]/, '');
  return baseName.replace(/^\d+_/, '') || baseName || display;
}

/** Map a list row back to the original entry stored in videos.json. */
function _removeTarget(v) {
  const ownAbs = v.abs_path || null;
  if (state.source === 'compressed') {
    const match = v.match || null;
    return { file: match?.file || v.file, absPath: match?.abs_path || ownAbs };
  }
  return { file: v.file, absPath: ownAbs };
}

async function _selectedVideoTargets(files) {
  const wanted = new Set((files || []).map(f => String(f)));
  return (state.videos || [])
    .filter(v => wanted.has(videoSelectionKey(v)))
    .map(_removeTarget);
}

async function _removeVideos(targets) {
  const list = (Array.isArray(targets) ? targets : []).filter(Boolean);
  if (!list.length) return null;
  try {
    const { removeSelectedVideos } = await import('./video-selection.js');
    const r = await api('GET', '/api/videos/selected');
    const videos = (r.videos || []).slice();
    const { remaining, removed, missing, ambiguous } = removeSelectedVideos(videos, list);
    if (!removed.length) {
      setStatus(ambiguous[0] || t('sidebar.video_not_found'), 'err');
      return null;
    }
    await api('PUT', '/api/videos/selected', { videos: remaining });
    await loadVideos();
    if (state.selectedFiles.length) {
      const present = new Set((state.videos || []).map(videoSelectionKey));
      const next = state.selectedFiles.filter(f => present.has(f));
      if (next.length !== state.selectedFiles.length) {
        state.selectedFiles = next;
        renderVideoList();
      }
    }
    const failed = missing.length + ambiguous.length;
    if (failed) {
      setStatus(t('sidebar.remove_partial', { removed: removed.length, failed }), 'warn');
    } else if (removed.length === 1) {
      setStatus(t('sidebar.removed', { name: _stripDisplay(list[0].file) }), 'ok');
    } else {
      setStatus(t('sidebar.removed_count', { count: removed.length }), 'ok');
    }
    return { removed: removed.length, failed };
  } catch (e) {
    setStatus(t('sidebar.remove_failed', { detail: e.message }), 'err');
    return null;
  }
}

export async function removeVideoFromProject(file, absPath = null) {
  return _removeVideos([{ file, absPath }]);
}

/** Remove every currently selected video from the project in one request. */
export async function removeSelectedVideosFromProject() {
  const files = (state.selectedFiles || []).slice();
  if (!files.length) {
    setStatus(t('sidebar.remove_selected_none'), 'warn');
    return null;
  }
  if (!(await confirmDialog(t('sidebar.remove_selected_confirm', { count: files.length }), { danger: true }))) return null;
  const targets = await _selectedVideoTargets(files);
  return _removeVideos(targets);
}

// ── Data loading ───────────────────────────────────────────────

let _videoLoadingTimer = null;
let _videoLoadingStartedAt = 0;
let _videoLoadingMessage = '';

export function videoLoadingStateHtml(message, elapsedSec = 0) {
  const elapsed = Number.isFinite(elapsedSec) && elapsedSec >= 1
    ? t('sidebar.elapsed_seconds', { seconds: Math.floor(elapsedSec) })
    : '';
  return `<li class="empty-state video-loading-state" role="status" aria-live="polite">
    <div class="loading">${escapeHtml(message)}${elapsed}</div>
    <div class="video-loading-skeleton" aria-hidden="true">
      <span class="video-loading-row"></span>
      <span class="video-loading-row"></span>
      <span class="video-loading-row"></span>
    </div>
    <p class="hint">${t('sidebar.scan_hint')}</p>
  </li>`;
}

function _stopVideoLoadingTimer() {
  if (_videoLoadingTimer) {
    clearInterval(_videoLoadingTimer);
    _videoLoadingTimer = null;
  }
}

export function hideVideoListLoading() {
  _stopVideoLoadingTimer();
  const ul = $('video-list');
  if (ul?.querySelector?.('.video-loading-state')) ul.innerHTML = '';
}

export function showVideoListLoading(message = t('sidebar.loading_videos')) {
  const ul = $('video-list');
  if (!ul) return;
  _stopVideoLoadingTimer();
  _videoLoadingStartedAt = Date.now();
  _videoLoadingMessage = message;
  const paint = () => {
    const elapsedSec = (Date.now() - _videoLoadingStartedAt) / 1000;
    ul.innerHTML = videoLoadingStateHtml(_videoLoadingMessage, elapsedSec);
  };
  paint();
  const countEl = $('video-count');
  if (countEl) countEl.textContent = '…';
  _videoLoadingTimer = setInterval(paint, 1000);
}

export function showVideoListError(message) {
  _stopVideoLoadingTimer();
  const ul = $('video-list');
  if (!ul) return;
  ul.innerHTML = `<li class="empty-state video-load-error">
    ${icon('video', 36)}
    <h4>${t('sidebar.video_load_failed')}</h4>
    <p>${escapeHtml(message || t('common.unknown_error'))}</p>
    <p style="margin-top:12px">
      <button class="sidebar-btn" id="empty-retry-videos" style="background:var(--accent);color:#fff;border:none;padding:7px 14px;border-radius:var(--radius-sm);cursor:pointer;font:inherit;font-size:var(--text-sm)">${t('sidebar.reload')}</button>
    </p>
  </li>`;
  const retryBtn = ul.querySelector('#empty-retry-videos');
  if (retryBtn) retryBtn.onclick = () => { loadVideos().catch(() => {}); };
}

export async function loadProjects() {
  try {
    const r = await api('GET', '/api/projects');
    state.projects = r.projects || [];
    const last = r.last_project;
    if (last && typeof last === 'object') {
      state.lastProject = last.name || null;
      state.lastProjectDir = last.project_dir || null;
    } else {
      state.lastProject = last || null;
      state.lastProjectDir = null;
    }
    if (state.lastProject && !state.lastProjectDir) {
      const match = state.projects.find(p => p.name === state.lastProject);
      if (match) state.lastProjectDir = match.project_dir || null;
    }
    state.currentProject = state.projects.find(p => p.is_current) || null;
    if (state.currentProject) {
      if (!state.currentProjectName) state.currentProjectName = state.currentProject.name;
      if (!state.currentProjectDir) state.currentProjectDir = state.currentProject.project_dir;
    }
    updateProjectSidebar();
  } catch (e) {
    state.projects = [];
    state.currentProject = null;
    state.currentProjectName = null;
    state.currentProjectDir = null;
    state.lastProject = null;
    state.lastProjectDir = null;
    updateProjectSidebar();
    setStatus(t('sidebar.project_list_failed', { detail: e.message }), 'err');
  }
}

export async function loadConfig() {
  const ac = beginLatest('config');
  try {
    const cfg = await api('GET', '/api/config', undefined, { signal: ac.signal });
    if (!isLatest('config', ac)) return;
    state.config = cfg;
  } catch (e) {
    if (isAbortError(e) || !isLatest('config', ac)) return;
    state.config = { project_dir: t('sidebar.load_failed_short'), output_dir: '' };
    setStatus(t('sidebar.config_load_failed', { detail: e.message }), 'err');
  } finally {
    endLatest('config', ac);
  }
  updateProjectSidebar();
  const projectName = state.currentProject?.name || state.currentProjectName || state.projectName || t('sidebar.select_project');
  $('proj-name').title = t('sidebar.project_dirs_tooltip', {
    name: projectName,
    projectDir: state.config.project_dir || '',
    outputDir: state.config.output_dir || '',
  });
}

/** Probe ffmpeg/ffprobe; stores result on state.deps (null if request fails). */
export async function loadFfmpegDeps() {
  try {
    state.deps = await api('GET', '/api/deps/ffmpeg');
  } catch {
    state.deps = null;
  }
  return state.deps;
}

export async function loadPlans() {
  const ac = beginLatest('plans');
  try {
    const r = await api('GET', '/api/plans', undefined, { signal: ac.signal });
    if (!isLatest('plans', ac)) return;
    state.availablePlans = r.plans || [];
  } catch (e) {
    if (isAbortError(e) || !isLatest('plans', ac)) return;
    state.availablePlans = [];
  } finally {
    endLatest('plans', ac);
  }
}

export function updateSelectBtnVisibility() {
  const btn = document.getElementById('btn-select-videos');
  if (!btn) return;
  const visible = state.videos.length > 0 && state.currentEntity === 'run';
  btn.style.display = visible ? 'flex' : 'none';
  if (!visible && state.selectionMode) {
    state.selectionMode = false;
    state.selectedFiles = [];
    renderVideoList();
  }
  // Always resync label: leaving run clears selectionMode but used to leave "取消选择".
  btn.innerHTML = selectVideosButtonHtml(state.selectionMode);
  btn.style.border = state.selectionMode ? '1px solid var(--warn)' : '';
}

export async function loadVideos() {
  showVideoListLoading(state.source === 'compressed' ? t('sidebar.scanning_compressed') : t('sidebar.scanning_original'));
  const ac = beginLatest('videos');
  try {
    const r = await api('GET', `/api/videos?source=${state.source}`, undefined, { signal: ac.signal });
    if (!isLatest('videos', ac)) return;
    state.videos = r.videos;
    state.groups = r.groups || {};
    clearVideoInfoCache();
    updateSelectBtnVisibility();
    renderVideoList();
    // Hint when project has no selected originals yet
    if (state.source === 'original' && state.videos.length === 0) {
      try {
        const sel = await api('GET', '/api/videos/selected', undefined, { signal: ac.signal });
        if (!isLatest('videos', ac)) return;
        const n = (sel.videos || []).length;
        if (n === 0) {
          setStatus(t('sidebar.no_videos_hint'), 'ok');
        }
      } catch (e) {
        if (isAbortError(e)) return;
      }
    }
  } catch (e) {
    if (isAbortError(e) || !isLatest('videos', ac)) return;
    showVideoListError(e.message || String(e));
    throw e;
  } finally {
    endLatest('videos', ac);
  }
}

let _filterInputBound = false;
export function initVideoFilterBar() {
  if (_filterInputBound) return;
  _filterInputBound = true;
  const input = $('video-filter-input');
  if (!input) return;
  input.addEventListener('input', () => {
    clearTimeout(state._filterDebounce);
    state._filterDebounce = setTimeout(() => {
      state.videoFilter.q = input.value;
      renderVideoList();
    }, 150);
  });
}

export async function loadProject() {
  const ac = beginLatest('project');
  try {
    const proj = await api('GET', '/api/project', undefined, { signal: ac.signal });
    if (!isLatest('project', ac)) return;
    if (proj.currentDay) state.currentDay = proj.currentDay;
    if (proj.source && proj.source !== state.source) {
      state.source = proj.source;
      state.videoFilter = { q: '', stage: '', mode: 'missing' };
      if ($('video-filter-input')) $('video-filter-input').value = '';
      $$('.source-toggle button').forEach(b => b.classList.toggle('active', b.dataset.source === state.source));
    }
    state.steps = proj.steps || {};
    state.projectName = proj.name || '';
    updateProjectSidebar();
    if (proj.lastEntity && ['video', 'plan', 'run', 'config', 'logs', 'tokens'].includes(proj.lastEntity)) {
      state.lastEntity = proj.lastEntity;
    }
    if (proj.lastVideo) state.lastVideo = proj.lastVideo;
  } catch (e) {
    if (isAbortError(e) || !isLatest('project', ac)) return;
    /* 非关键, 静默忽略 */
  } finally {
    endLatest('project', ac);
  }
}

export async function saveProject(extra) {
  try {
    const { buildProjectSavePayload } = await import('./session-restore.js');
    await api('PUT', '/api/project', buildProjectSavePayload({
      currentDay: state.currentDay,
      source: state.source,
      currentEntity: state.currentEntity,
      currentVideo: state.currentVideo,
      projectName: state.projectName,
    }, extra || {}));
  } catch (e) { /* 静默 */ }
}

// ── Video list rendering ───────────────────────────────────────

function scrollActiveVideoIntoView() {
  requestAnimationFrame(() => {
    const active = document.querySelector('#video-list .video-item.active');
    if (active) {
      active.scrollIntoView({ block: 'nearest' });
    }
  });
}

export function renderVideoList() {
  const ul = $('video-list');
  if (!ul) return;
  hideVideoListLoading();
  ul.innerHTML = '';
  renderOfflineSummary();
  if (!state.videos.length) {
    const isCompressedEmpty = state.source === 'compressed';
    if (isCompressedEmpty) {
      ul.innerHTML = `
        <li class="empty-state">
          ${icon('folder', 36)}
          <h4>${t('sidebar.empty_compressed_title')}</h4>
          <p>${t('sidebar.empty_compressed_body')}</p>
          <p class="hint">${t('sidebar.empty_compressed_hint')}</p>
          <p style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
            <button class="sidebar-btn" id="empty-go-original" style="background:var(--accent);color:#fff;border:none;padding:7px 14px;border-radius:var(--radius-sm);cursor:pointer;font:inherit;font-size:var(--text-sm)">${icon('folder', 14)} ${t('sidebar.go_original')}</button>
            <button class="sidebar-btn" id="empty-go-run" title="${t('sidebar.compress_step_title')}" style="background:var(--bg-surface-2);color:var(--text-primary);border:1px solid var(--border);padding:7px 14px;border-radius:var(--radius-sm);cursor:pointer;font:inherit;font-size:var(--text-sm)">${icon('play', 14)} ${t('sidebar.go_compress')}</button>
          </p>
        </li>
      `;
      const goOriginalBtn = ul.querySelector('#empty-go-original');
      if (goOriginalBtn) {
        goOriginalBtn.onclick = () => {
          import('./sidebar.js').then(m => m.switchToOriginalThenCompress());
        };
      }
      const goRunBtn = ul.querySelector('#empty-go-run');
      if (goRunBtn) {
        goRunBtn.onclick = () => {
          import('./sidebar.js').then(m => m.goToRunTab());
        };
      }
    } else {
      ul.innerHTML = `
        <li class="empty-state">
          ${icon('video', 36)}
          <h4>${t('sidebar.empty_original_title')}</h4>
          <p>${t('sidebar.empty_original_body')}</p>
          <p class="hint">${t('sidebar.empty_original_hint')}</p>
          <p style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
            <button class="sidebar-btn" id="empty-add-videos" style="background:var(--accent);color:#fff;border:none;padding:7px 14px;border-radius:var(--radius-sm);cursor:pointer;font:inherit;font-size:var(--text-sm)">${icon('plus', 14)} ${t('sidebar.add_videos')}</button>
          </p>
        </li>
      `;
      const addBtn = ul.querySelector('#empty-add-videos');
      if (addBtn) {
        addBtn.onclick = () => {
          import('./sidebar-video-manage.js').then(m => m.openVideoManager());
        };
      }
    }
    const countEl = $('video-count');
    if (countEl) countEl.textContent = '(0/0)';
    renderFilterChips(renderVideoList);
    renderStageCountBar(renderVideoList);
    return;
  }

  const visible = filterVideos(state.videos, state.videoFilter, state.source);

  const countEl = $('video-count');
  if (countEl) countEl.textContent = `(${visible.length}/${state.videos.length})`;

  renderFilterChips(renderVideoList);
  renderStageCountBar(renderVideoList);

  if (state.selectionMode) {
    const selectable = visible.filter(v => !v.missing);
    const allSelected = selectable.length > 0 && selectable.every(v => state.selectedFiles.includes(videoSelectionKey(v)));
    const totalSelectable = state.videos.filter(v => !v.missing).length;
    const headerDiv = document.createElement('div');
    headerDiv.className = 'selection-header';
    headerDiv.innerHTML = `
      <span class="selection-count" title="${t('sidebar.drag_select_hint')}">${t('sidebar.selected_count', { selected: state.selectedFiles.length, total: totalSelectable })}</span>
      <span class="selection-action danger" data-action="remove">${t('sidebar.remove_selected')}</span>
      <span class="selection-action" data-action="all">${allSelected ? t('sidebar.deselect_all') : t('sidebar.select_all')}</span>
    `;
    const removeAction = headerDiv.querySelector('[data-action="remove"]');
    if (state.selectedFiles.length === 0) {
      removeAction.classList.add('disabled');
    } else {
      removeAction.onclick = () => { removeSelectedVideosFromProject(); };
    }
    headerDiv.querySelector('[data-action="all"]').onclick = () => {
      state.selectedFiles = toggleSelectionFor(state.selectedFiles, selectable.map(videoSelectionKey));
      renderVideoList();
    };
    ul.appendChild(headerDiv);
  }

  if (visible.length === 0 && state.videos.length > 0) {
    const li = document.createElement('li');
    li.className = 'empty-state';
    li.innerHTML = `
      <p class="hint">${t('sidebar.no_filter_match')}</p>
      <button type="button" class="sidebar-btn" id="btn-clear-video-filter">${t('sidebar.clear_filter')}</button>
    `;
    li.querySelector('#btn-clear-video-filter').onclick = () => {
      state.videoFilter.q = '';
      state.videoFilter.stage = '';
      state.videoFilter.mode = 'missing';
      if ($('video-filter-input')) $('video-filter-input').value = '';
      renderVideoList();
    };
    ul.appendChild(li);
    return;
  }

  const groups = {};
  for (const v of visible.filter(v => v.group_key)) {
    (groups[v.group_key] ??= []).push(v);
  }
  const renderedGroups = new Set();

  for (const v of visible) {
    if (!v.group_key) {
      ul.appendChild(renderVideoItem(v, { relinkVideo, removeVideoFromProject, rerenderVideoList: renderVideoList }));
      continue;
    }
    const key = v.group_key;
    if (renderedGroups.has(key)) continue;
    renderedGroups.add(key);
    const items = groups[key] || [];
    if (items.some(item => item.file === state.currentVideo)) {
      state.expandedGroups[key] = true;
    }
    const header = document.createElement('li');
    header.className = 'video-group-header';
    const isExpanded = state.expandedGroups[key] !== false;
    header.innerHTML = `
      <span class="group-toggle">${isExpanded ? '▼' : '▶'}</span>
      <span class="group-name">${escapeHtml(key)}</span>
      <span class="group-count-badge">${t('sidebar.segment_count', { count: items.length })}</span>
    `;
    header.onclick = (e) => {
      e.stopPropagation();
      const wasExpanded = state.expandedGroups[key] !== false;
      state.expandedGroups[key] = !wasExpanded;
      const childUl = header.nextElementSibling;
      if (childUl) {
        childUl.style.display = state.expandedGroups[key] ? '' : 'none';
        header.querySelector('.group-toggle').textContent = state.expandedGroups[key] ? '▼' : '▶';
      }
    };
    ul.appendChild(header);

    const childUl = document.createElement('ul');
    childUl.className = 'video-group-children';
    childUl.style.display = isExpanded ? '' : 'none';
    for (const v of items) {
      childUl.appendChild(renderVideoItem(v, { relinkVideo, removeVideoFromProject, rerenderVideoList: renderVideoList }));
    }
    ul.appendChild(childUl);
  }

  scrollActiveVideoIntoView();
  updateRunFilesBadge();
}

// The sidebar is rendered from JS strings, so re-render it on a language switch
// instead of leaving stale copy behind.
onLocaleChange(() => {
  updateProjectSidebar();
  updateSelectBtnVisibility();
  renderVideoList();
});

export {
  videoDisplayName,
  videoThumbHtml,
};
