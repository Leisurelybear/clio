import { state } from './state.js';
import {
  $, $$, escapeHtml, setStatus, updateProjectSidebar,
} from './utils.js';
import { api, icon } from './api.js';
import { beginLatest, endLatest, isLatest, isAbortError } from './latest.js';
import { updateRunFilesBadge } from './runner.js';
import { summarizeOfflineVideos } from './offline-media.js';
import {
  buildVideoMenuItems,
  videoMenuItemsToHtml,
} from './video-menu.js';
import {
  buildVideoStageStatuses,
  chipDefsForSource,
  countChipStats,
  countStageSummary,
  matchVideoSearch,
  videoMissingStage,
  videoStageDone,
  STAGE_CELLS,
} from './sidebar-video-filter.js';
import { selectVideosButtonHtml } from './select-btn.js';

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
    setStatus(`已重新关联: ${newPath}`, 'ok');
    await loadVideos();
    return r;
  }
  setStatus('重新关联失败: ' + (r.error || '未知错误'), 'err');
  return r;
}

// ── Video removal helper ──────────────────────────────────────

export async function removeVideoFromProject(file, absPath = null) {
  try {
    const { findSelectedVideoIndex } = await import('./video-selection.js');
    const r = await api('GET', '/api/videos/selected');
    const videos = (r.videos || []).slice();
    const display = String(file || '');
    const baseName = display.replace(/^.*[\\/]/, '');
    const stripped = baseName.replace(/^\d+_/, '');
    const found = findSelectedVideoIndex(videos, { file, absPath });
    if (found.index === -1) {
      setStatus(found.error || '未在项目视频列表中找到该文件', 'err');
      return;
    }
    videos.splice(found.index, 1);
    await api('PUT', '/api/videos/selected', { videos });
    await loadVideos();
    setStatus(`已移除 ${stripped || baseName || file}`, 'ok');
  } catch (e) {
    setStatus('移除失败: ' + e.message, 'err');
  }
}

// ── Data loading ───────────────────────────────────────────────

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
    state.config = { project_dir: '(加载失败)', output_dir: '' };
  } finally {
    endLatest('config', ac);
  }
  $('proj-name').textContent = state.config.project_dir || '';
  $('proj-name').title = `project: ${state.config.project_dir || ''}\noutput: ${state.config.output_dir || ''}`;
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
  const ac = beginLatest('videos');
  try {
    const r = await api('GET', `/api/videos?source=${state.source}`, undefined, { signal: ac.signal });
    if (!isLatest('videos', ac)) return;
    state.videos = r.videos;
    state.groups = r.groups || {};
    updateSelectBtnVisibility();
    renderVideoList();
    // Hint when project has no selected originals yet
    if (state.source === 'original' && state.videos.length === 0) {
      try {
        const sel = await api('GET', '/api/videos/selected', undefined, { signal: ac.signal });
        if (!isLatest('videos', ac)) return;
        const n = (sel.videos || []).length;
        if (n === 0) {
          setStatus('项目尚无视频，点击「添加视频」从磁盘选择素材（或运行 python main.py migrate）', 'ok');
        }
      } catch (e) {
        if (isAbortError(e)) return;
      }
    }
  } catch (e) {
    if (isAbortError(e) || !isLatest('videos', ac)) return;
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

let _portalDropdown = null;
let _portalCloseHandler = null;

function scrollActiveVideoIntoView() {
  requestAnimationFrame(() => {
    const active = document.querySelector('#video-list .video-item.active');
    if (active) {
      active.scrollIntoView({ block: 'nearest' });
    }
  });
}

/** Cover thumbnail HTML for video list (falls back to video icon). */
export function videoThumbHtml(v) {
  const name = String(v?.cover_file || '').replace(/\\/g, '/').split('/').pop();
  if (!name) {
    return `<div class="video-thumb">${icon('video')}</div>`;
  }
  const params = new URLSearchParams();
  params.set('file', name);
  if (state.currentProjectName) params.set('project', state.currentProjectName);
  if (state.currentProjectDir) params.set('project_dir', state.currentProjectDir);
  const tok = sessionStorage.getItem('api_token');
  if (tok) params.set('token', tok);
  const src = `/api/cover?${params.toString()}`;
  // Fallback icon under img; onerror on the img swaps to icon via .cover-error.
  return `<div class="video-thumb has-cover" title="AI 封面">
    <img src="${escapeHtml(src)}" alt="" loading="lazy" decoding="async">
    <span class="video-thumb-fallback">${icon('video')}</span>
  </div>`;
}

function bindCoverThumbFallback(root) {
  const img = root?.querySelector?.('.video-thumb.has-cover img');
  if (!img) return;
  img.addEventListener('error', () => {
    const thumb = img.closest('.video-thumb');
    if (!thumb) return;
    thumb.classList.add('cover-error');
    thumb.classList.remove('has-cover');
  }, { once: true });
}

function renderVideoItem(v) {
  const li = document.createElement('li');
  let checkboxHtml = '';
  let selectedClass = '';
  if (state.selectionMode) {
    const isSelected = state.selectedFiles.includes(v.file);
    selectedClass = isSelected ? ' selected' : '';
    const disabled = v.missing ? 'disabled title="离线视频不可选"' : '';
    checkboxHtml = `<input type="checkbox" class="video-checkbox" data-file="${escapeHtml(v.file)}" ${isSelected ? 'checked' : ''} ${disabled}>`;
  }
  li.className = 'video-item' + selectedClass;
  if (state.currentVideo === v.file) li.classList.add('active');
  if (!v.match) li.classList.add('no-match');
  if (v.missing) li.classList.add('missing');

  let display = v.file.replace(/^\d+_/, '');
  if (v.segment_label) {
    display = display.replace(/_seg\d+$/i, '') + ` [seg ${v.segment_label}]`;
  }
  if (v.missing) {
    display = display + ' (离线)';
  }

  const tCls = v.text_json ? 'has' : 'miss';
  const sCls = v.script_json ? 'has' : 'miss';
  const tTransCls = v.transcript_file ? 'has' : 'miss';
  const tLabel = v.text_json ? `${icon('check', 12)} texts` : '· texts';
  const sLabel = v.script_json ? `${icon('check', 12)} voiceover` : '· voiceover';
  const tTransLabel = v.transcript_file ? `${icon('check', 12)} trans.` : '· trans.';
  const counterpartLabel = state.source === 'compressed' ? '原' : '压';
  let matchBadge;
  if (v.match) {
    if (v.match.missing) {
      matchBadge = `<span class="match-badge miss" title="${escapeHtml(v.match.abs_path || v.match.file || '')}">→ ${counterpartLabel}: 离线</span>`;
    } else if (v.segment_matches && v.segment_matches.length > 1) {
      const segList = v.segment_matches.map(m => m.file).join(', ');
      matchBadge = `<button type="button" class="match-badge match-jump" title="${escapeHtml(segList)}">→ ${counterpartLabel}: ${escapeHtml(v.index || '')}/${v.segment_matches.length} 段</button>`;
    } else {
      matchBadge = `<button type="button" class="match-badge match-jump" title="${escapeHtml(v.match.file)}">→ ${counterpartLabel}: ${escapeHtml(v.match.file)}</button>`;
    }
  } else {
    matchBadge = `<span class="match-badge miss" title="没有对应的${state.source === 'compressed' ? '原视频' : '压缩视频'}">无对应</span>`;
  }
  const stepStatuses = buildVideoStageStatuses(v, state.source);
  const stepDots = STAGE_CELLS
    .map(c => `<span class="video-step-dot ${stepStatuses[c.key] ? 'done' : 'pending'}" title="${c.label}: ${stepStatuses[c.key] ? '完成' : '待处理'}"></span>`)
    .join('');
  const menuHtml = videoMenuItemsToHtml(buildVideoMenuItems(v, state.source, state.deps));

  const durHtml = (v.duration_sec != null && Number(v.duration_sec) >= 0)
    ? `<span class="video-duration">${Math.round(Number(v.duration_sec))}s</span>`
    : '';

  li.innerHTML = `${videoThumbHtml(v)}
    <div class="video-info">
      <div class="video-name">${checkboxHtml}${v.index ? '[' + v.index + '] ' : ''}${escapeHtml(display)}${durHtml}</div>
      <div class="video-step-dots">${stepDots}</div>
      ${v.title ? `<div class="video-title">${escapeHtml(v.title)}</div>` : ''}
      <div class="video-match">${matchBadge}</div>
    </div>
      <div class="video-actions">
        <button class="menu-btn" title="操作">⋮</button>
        <div class="menu-dropdown">
          ${menuHtml}
        </div>
      </div>
  `;
  bindCoverThumbFallback(li);

  li.onclick = (e) => {
    if (e.target.closest('.match-jump')) return;
    if (e.target.closest('.video-actions')) return;
    if (v.missing) {
      setStatus('该视频文件当前不可用，请重新关联路径', 'warn');
      relinkVideo(v.file, v.abs_path || (v.match && v.match.abs_path) || null);
      return;
    }
    if (state.selectionMode) {
      if (e.target.closest('.video-checkbox')) return;
      const cb = li.querySelector('.video-checkbox');
      if (cb) { cb.checked = !cb.checked; cb.dispatchEvent(new Event('change', { bubbles: true })); }
      return;
    }
    import('./sidebar.js').then(mod => mod.selectVideo(v.file));
  };

  const matchJump = li.querySelector('.match-jump');
  if (matchJump) {
    matchJump.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      import('./sidebar.js').then(mod => mod.jumpToCounterpart(v));
    };
  }

  const menuBtn = li.querySelector('.menu-btn');
  const dropdown = li.querySelector('.menu-dropdown');
  menuBtn.onclick = (e) => {
    e.stopPropagation();
    if (_portalDropdown) { _portalDropdown.remove(); _portalDropdown = null; }
    if (_portalCloseHandler) { document.removeEventListener('click', _portalCloseHandler); _portalCloseHandler = null; }
    const rect = menuBtn.getBoundingClientRect();
    const clone = dropdown.cloneNode(true);
    clone.classList.add('open');
    clone.style.cssText = 'position:fixed;top:' + (rect.bottom + 4) + 'px;right:auto;left:' + Math.max(4, rect.right - 160) + 'px;z-index:10000;min-width:160px;width:auto;';
    document.body.appendChild(clone);
    _portalDropdown = clone;
    clone.querySelectorAll('.menu-item').forEach(item => {
      item.onclick = async (ev) => {
        ev.stopPropagation(); if (item.disabled) return;
        clone.remove(); _portalDropdown = null;
        if (_portalCloseHandler) { document.removeEventListener('click', _portalCloseHandler); _portalCloseHandler = null; }
        const task = item.dataset.action;
        const file = v.file;
        if (task === 'relink') {
          await relinkVideo(file, v.abs_path || (v.match && v.match.abs_path) || null);
          return;
        }
        if (task === 'remove') {
          if (confirm(`确定从项目中移除 ${file} 吗？`)) {
            await removeVideoFromProject(file, v.abs_path || (v.match && v.match.abs_path) || null);
          }
          return;
        }
        setStatus(`正在重跑 ${task} (${file})...`, 'ok');
        try {
          const r = await api('POST', '/api/rerun', {
            video: file,
            task: task,
            source: state.source,
            index: v.index || undefined,
            abspath: v.abs_path || (v.match && v.match.abs_path) || undefined,
          });
          if (r.ok) {
            setStatus(r.message || `${task} 已启动`, 'ok');
            import('./sidebar-rerun.js').then(mod => mod.showRerunProgress(task, file));
          } else { throw new Error(r.error || '重跑失败'); }
        } catch (e) { setStatus('重跑失败: ' + e.message, 'err'); }
      };
    });
    if (!_portalCloseHandler) {
      _portalCloseHandler = (ev) => {
        if (_portalDropdown && !_portalDropdown.contains(ev.target) && !ev.target.closest('.menu-btn')) {
          _portalDropdown.remove(); _portalDropdown = null;
          document.removeEventListener('click', _portalCloseHandler);
          _portalCloseHandler = null;
        }
      };
      setTimeout(() => document.addEventListener('click', _portalCloseHandler), 0);
    }
  };

  if (state.selectionMode) {
    const cb = li.querySelector('.video-checkbox');
    if (cb) {
      cb.addEventListener('change', (e) => {
        e.stopPropagation();
        if (cb.checked) {
          if (!state.selectedFiles.includes(v.file)) state.selectedFiles.push(v.file);
        } else {
          state.selectedFiles = state.selectedFiles.filter(f => f !== v.file);
        }
        renderVideoList();
      });
    }
  }

  return li;
}

export function renderVideoList() {
  const ul = $('video-list');
  if (!ul) return;
  ul.innerHTML = '';
  renderOfflineSummary();
  if (state.selectionMode) {
    const headerDiv = document.createElement('div');
    headerDiv.className = 'selection-header';
    const selectable = state.videos.filter(v => !v.missing);
    const allSelected = selectable.length > 0 && selectable.every(v => state.selectedFiles.includes(v.file));
    headerDiv.innerHTML = `
      <span class="selection-count">已选: ${state.selectedFiles.length}/${selectable.length}</span>
      <span class="selection-action" data-action="all">${allSelected ? '取消全选' : '全选'}</span>
    `;
    headerDiv.querySelector('[data-action="all"]').onclick = () => {
      if (allSelected) {
        state.selectedFiles = [];
      } else {
        state.selectedFiles = selectable.map(v => v.file);
      }
      renderVideoList();
    };
    ul.appendChild(headerDiv);
  }
  if (!state.videos.length) {
    const isCompressedEmpty = state.source === 'compressed';
    if (isCompressedEmpty) {
      ul.innerHTML = `
        <li class="empty-state">
          ${icon('folder', 36)}
          <h4>暂无压缩视频</h4>
          <p>未找到压缩后的视频文件（output/compressed/）</p>
          <p class="hint">请先压缩原视频，或切换到「原视频」视图查看素材</p>
          <p style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
            <button class="sidebar-btn" onclick="switchToOriginalThenCompress()" style="background:var(--accent);color:#fff;border:none;padding:7px 14px;border-radius:var(--radius-sm);cursor:pointer;font:inherit;font-size:var(--text-sm)">${icon('folder', 14)} 切换到原视频</button>
            <button class="sidebar-btn" onclick="goToRunTab()" title="运行流水线中的压缩步骤" style="background:var(--bg-surface-2);color:var(--text-primary);border:1px solid var(--border);padding:7px 14px;border-radius:var(--radius-sm);cursor:pointer;font:inherit;font-size:var(--text-sm)">${icon('play', 14)} 去压缩视频</button>
          </p>
        </li>
      `;
    } else {
      ul.innerHTML = `
        <li class="empty-state">
          ${icon('video', 36)}
          <h4>暂无视频素材</h4>
          <p>项目 <code>videos.json</code> 中还没有选中的原始视频</p>
          <p class="hint">点击上方「添加视频」从磁盘勾选素材；旧项目可运行 <code>python main.py migrate</code></p>
          <p style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
            <button class="sidebar-btn" id="empty-add-videos" style="background:var(--accent);color:#fff;border:none;padding:7px 14px;border-radius:var(--radius-sm);cursor:pointer;font:inherit;font-size:var(--text-sm)">${icon('plus', 14)} 添加视频</button>
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
    renderFilterChips();
    renderStageCountBar();
    return;
  }

  const q = state.videoFilter?.q || '';
  const stage = state.videoFilter?.stage || '';
  const mode = state.videoFilter?.mode || 'missing';
  const visible = state.videos.filter((v) =>
    matchVideoSearch(v, q) && (!stage || (
      mode === 'done'
        ? videoStageDone(v, state.source, stage)
        : videoMissingStage(v, state.source, stage)
    ))
  );

  const countEl = $('video-count');
  if (countEl) countEl.textContent = `(${visible.length}/${state.videos.length})`;

  renderFilterChips();
  renderStageCountBar();

  if (visible.length === 0 && state.videos.length > 0) {
    const li = document.createElement('li');
    li.className = 'empty-state';
    li.innerHTML = `
      <p class="hint">没有匹配的视频，试试清除筛选</p>
      <button type="button" class="sidebar-btn" id="btn-clear-video-filter">清除筛选</button>
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
      ul.appendChild(renderVideoItem(v));
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
      <span class="group-count-badge">(${items.length} 段)</span>
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
      childUl.appendChild(renderVideoItem(v));
    }
    ul.appendChild(childUl);
  }

  scrollActiveVideoIntoView();
  updateRunFilesBadge();
}

function renderOfflineSummary() {
  const box = $('offline-summary');
  if (!box) return;
  // Only meaningful in original view where offline paths are listed
  const summary = summarizeOfflineVideos(state.videos || []);
  if (!summary.count || state.source !== 'original') {
    box.hidden = true;
    box.innerHTML = '';
    return;
  }
  box.hidden = false;
  box.innerHTML = `
    <div class="offline-summary-inner">
      <span class="offline-summary-text">⚠ ${summary.count} 个视频离线</span>
      <button type="button" class="sidebar-btn offline-batch-btn" id="btn-batch-relink" title="按文件名批量重新关联">批量关联</button>
    </div>
  `;
  const btn = box.querySelector('#btn-batch-relink');
  if (btn) {
    btn.onclick = () => {
      import('./sidebar-batch-relink.js').then(m => m.openBatchRelinkModal());
    };
  }
}

function renderFilterChips() {
  const box = $('video-filter-chips');
  if (!box) return;
  const all = state.videos.length;
  const stats = countChipStats(state.videos, state.source);
  box.innerHTML = '';
  const isMissingFilter = state.videoFilter.mode !== 'done';
  const allBtn = document.createElement('button');
  allBtn.type = 'button';
  allBtn.className = 'video-filter-chip' + (!state.videoFilter.stage ? ' active' : '');
  allBtn.textContent = `全部 ${all}`;
  allBtn.onclick = () => {
    state.videoFilter.stage = '';
    state.videoFilter.mode = 'missing';
    renderVideoList();
  };
  box.appendChild(allBtn);
  for (const s of stats) {
    if (s.count === 0) continue;
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'video-filter-chip' + (
      isMissingFilter && state.videoFilter.stage === s.key ? ' active' : ''
    );
    b.textContent = `${s.label} ${s.count}`;
    b.onclick = () => {
      state.videoFilter.stage = state.videoFilter.stage === s.key ? '' : s.key;
      state.videoFilter.mode = 'missing';
      renderVideoList();
    };
    box.appendChild(b);
  }
}

function renderStageCountBar() {
  const box = $('stage-count-bar');
  if (!box) return;
  box.innerHTML = '';
  if (!state.videos.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const summary = countStageSummary(state.videos, state.source);
  const isDoneFilter = state.videoFilter.mode === 'done';
  for (const s of summary) {
    const cell = document.createElement('div');
    const isActive = isDoneFilter && state.videoFilter.stage === s.key;
    cell.className = 'stage-count-cell' + (s.done === 0 ? ' empty' : '') + (isActive ? ' active' : '');
    cell.innerHTML = `<span class="stage-count-num">${s.done}/${s.total}</span>${s.label}`;
    if (s.done === 0) {
      cell.title = `${s.label}: 尚无完成`;
    } else if (s.done === s.total) {
      cell.title = isDoneFilter && isActive ? `${s.label}: 全部 ${s.done} 个已完成` : `${s.label}: 全部完成`;
    } else {
      cell.title = `${s.label}: 已完成 ${s.done}，缺 ${s.total - s.done}`;
    }
    cell.onclick = () => {
      if (s.done === 0) return;
      const activeNow = state.videoFilter.stage === s.key && state.videoFilter.mode === 'done';
      if (activeNow) {
        state.videoFilter.stage = '';
        state.videoFilter.mode = 'missing';
      } else {
        state.videoFilter.stage = s.key;
        state.videoFilter.mode = 'done';
      }
      renderVideoList();
    };
    box.appendChild(cell);
  }
}
