import { state } from './state.js';
import { $, $$, escapeHtml, setStatus, updateSidebarDay, clearDirty } from './utils.js';
import { api, submitToken } from './api.js';
import { initLayout } from './layout.js';
import { initTheme, toggleTheme } from './theme.js';
import { initLanguageSwitcher } from './language-switcher.js';
import { pickFolder, pickFile, applyPickToInput, initDesktopPickers } from './desktop-pick.js';
import { addToast } from './toast.js';
import { updateRuntimeWarnings } from './runtime-warnings.js';
import { setupPlayer } from './viewer.js';
import { save, initProjectConfig, renderActiveTab, refineCurrentFile } from './editor.js';
import {
  loadProjects,
  loadConfig,
  loadFfmpegDeps,
  loadProject,
  loadPlans,
  loadVideos,
  renderVideoList,
  selectVideo,
  selectPlan,
  selectRun,
  selectConfig,
  selectLogs,
  selectTokens,
  selectTasks,
  setSource,
  switchToOriginalThenCompress,
  goToRunTab,
  toggleSelection,
  hideRerunProgress,
} from './sidebar.js';
import { showVideoListLoading, hideVideoListLoading } from './sidebar-data.js';
import { resolveSessionRestore } from './session-restore.js';
import { shouldConfirmDirtyTabSwitch } from './editor-save.js';
import { stripQueryParams } from './url-params.js';
import { initNotificationCenter, registerNotification } from './notification-center.js';
import { startTaskStream, loadTasks } from './task-center.js';
import { openFfmpegInstallDialog } from './ffmpeg-install.js';
import { initI18n, readStoredLocale, t } from './i18n.js';

// Expose functions referenced by inline onclick handlers in HTML
window.switchToOriginalThenCompress = switchToOriginalThenCompress;
window.goToRunTab = goToRunTab;
window.initProjectConfig = initProjectConfig;
window.refineCurrentFile = refineCurrentFile;
window.addToast = addToast;

let _orphanedCutBackups = [];
let _missingKeys = null;
let _unboundTasks = [];

async function refreshRuntimeWarningsBanner() {
  try {
    const r = await api('GET', '/api/deps/keys');
    _missingKeys = Array.isArray(r.missing) ? r.missing : [];
    _unboundTasks = Array.isArray(r.unbound_tasks) ? r.unbound_tasks : [];
  } catch { /* keep the last successful probe during a transient outage */ }
  try {
    const r = await api('GET', '/api/cut/orphaned-backups');
    _orphanedCutBackups = Array.isArray(r.items) ? r.items : [];
  } catch { /* keep the last successful probe during a transient outage */ }
  updateRuntimeWarnings(state.config, {
    orphanedCutBackups: _orphanedCutBackups,
    ffmpegDeps: state.deps,
    missingKeys: _missingKeys,
    unboundTasks: _unboundTasks,
    onAction: handleRuntimeWarningAction,
  });
}

/** Re-probe ffmpeg, refresh banner (keeps orphan warnings), re-render menus. */
async function refreshFfmpegDepsUi() {
  await loadFfmpegDeps();
  await refreshRuntimeWarningsBanner();
  renderVideoList();
  return state.deps;
}
window.refreshFfmpegDepsUi = refreshFfmpegDepsUi;

async function handleRuntimeWarningAction(actionId) {
  if (actionId === 'show-ffmpeg-help') {
    showFfmpegHelp();
    return;
  }
  if (actionId === 'go-settings-keys') {
    await selectConfig();
    state.configTab = 'global';
    clearDirty();
    const { renderConfig } = await import('./editor-config.js');
    renderConfig();
    return;
  }
  if (actionId === 'go-settings-tasks') {
    await selectConfig();
    state.configTab = 'global';
    clearDirty();
    const { renderConfig } = await import('./editor-config.js');
    renderConfig();
    requestAnimationFrame(() => {
      document.querySelector('[data-section-key="global.task-binding"]')?.scrollIntoView({ block: 'start' });
    });
    return;
  }
  if (actionId !== 'restore-cut-backups') return;
  if (!_orphanedCutBackups.length) {
    setStatus(t('cut_backup.none'), 'warn');
    return;
  }
  const n = _orphanedCutBackups.length;
  const conflicts = (_orphanedCutBackups || []).filter((it) => it.conflict === 'true' || it.conflict === true);
  if (!confirm(t('cut_backup.restore_confirm', { count: n }))) {
    return;
  }
  try {
    let body = {};
    if (conflicts.length) {
      const keepNew = confirm(t('cut_backup.conflict_confirm', { count: conflicts.length }));
      body = { keep_target: keepNew };
    }
    const r = await api('POST', '/api/cut/restore-backups', body);
    const count = r.count || (r.restored || []).length || 0;
    const errN = (r.errors || []).length;
    setStatus(
      errN ? t('cut_backup.restored_failed', { count, failed: errN }) : t('cut_backup.processed', { count }),
      errN ? 'warn' : 'ok',
    );
    addToast(
      errN ? t('cut_backup.toast_failed', { count, failed: errN }) : t('cut_backup.toast_processed', { count }),
      errN ? 'warning' : 'success',
      undefined,
      { persist: false },
    );
  } catch (e) {
    if (e.status === 409 && e.body && e.body.conflicts && e.body.conflicts.length) {
      const keepNew = confirm(t('cut_backup.conflict_confirm', { count: e.body.conflicts.length }));
      try {
        const r = await api('POST', '/api/cut/restore-backups', { keep_target: keepNew });
        const count = r.count || (r.restored || []).length || 0;
        setStatus(t('cut_backup.processed', { count }), 'ok');
        addToast(t('cut_backup.toast_processed', { count }), 'success', undefined, { persist: false });
      } catch (e2) {
        setStatus(t('cut_backup.restore_failed', { detail: e2.message }), 'err');
        addToast(t('cut_backup.restore_failed', { detail: e2.message }), 'error', 6000, { title: t('cut_backup.restore_failed_title') });
      }
    } else {
      setStatus(t('cut_backup.restore_failed', { detail: e.message }), 'err');
      addToast(t('cut_backup.restore_failed', { detail: e.message }), 'error', 6000, { title: t('cut_backup.restore_failed_title') });
    }
  }
  await refreshRuntimeWarningsBanner();
}

/** Show the managed installer with manual fallback guidance. */
function showFfmpegHelp() {
  openFfmpegInstallDialog({ refreshDeps: refreshFfmpegDepsUi });
}

async function init() {
  await initI18n(readStoredLocale());

  // 从 URL 读取 project + project_dir 参数
  const urlParams = new URLSearchParams(window.location.search);
  const urlProject = urlParams.get('project');
  const urlProjectDir = urlParams.get('project_dir');
  const urlEntity = urlParams.get('entity');
  const urlVideo = urlParams.get('video');
  const urlDay = urlParams.get('day');
  const urlTaskId = urlParams.get('task_id');
  if (urlProject) {
    state.currentProjectName = urlProject;
  }
  if (urlProjectDir) {
    state.currentProjectDir = urlProjectDir;
  }

  // Auto-capture token from URL (keep project selectors; only drop token).
  const urlToken = urlParams.get('token');
  if (urlToken) {
    sessionStorage.setItem('api_token', urlToken);
    const cleaned = stripQueryParams(location.search, ['token']);
    history.replaceState(null, '', location.pathname + cleaned + (location.hash || ''));
  }

  showVideoListLoading(t('project.loading'));
  initLayout();
  initTheme();
  initDesktopPickers(document);
  initNotificationCenter();
  window.addEventListener('clio:notification-action', (event) => {
    void handleRuntimeWarningAction(event.detail?.id);
  });

  // 新建/打开项目模态框（必须在 try 之前绑定，空状态时也会用到）
  const newModal = $('modal-new-project');
  $('btn-new-project').onclick = () => { newModal.style.display = 'flex'; };
  $('np-cancel').onclick = () => { newModal.style.display = 'none'; };
  newModal.querySelector('.modal-backdrop').onclick = null;
  $('np-create').onclick = async () => {
    const name = $('np-name').value.trim();
    const projectDir = $('np-input-dir').value.trim();
    const outputDir = $('np-output-dir').value.trim();
    if (!name || !projectDir) { setStatus(t('project.need_name_dir'), 'warn'); return; }
    const createBtn = $('np-create');
    if (createBtn.disabled) return;
    createBtn.disabled = true;
    const prevLabel = createBtn.textContent;
    createBtn.textContent = t('project.creating');
    try {
      const body = { name, project_dir: projectDir };
      if (outputDir) body.output_dir = outputDir;
      const r = await api('POST', '/api/project/create', body);
      if (r.ok) {
        await registerNotification({
          message: t('project.created', { name }),
          severity: 'success',
          title: t('status.operation_complete'),
          sourceType: 'ui_status',
          projectName: name,
          projectDir,
          link: `?project=${encodeURIComponent(name)}&project_dir=${encodeURIComponent(projectDir)}`,
        });
        newModal.style.display = 'none';
        window.location.search = `?project=${encodeURIComponent(name)}&project_dir=${encodeURIComponent(projectDir)}`;
      } else {
        setStatus(t('project.create_failed', { detail: r.error || t('common.unknown_error') }), 'err');
      }
    } catch (e) {
      setStatus(t('project.create_failed', { detail: e.message }), 'err');
    } finally {
      createBtn.disabled = false;
      createBtn.textContent = prevLabel;
    }
  };

  const openModal = $('modal-open-project');
  $('btn-open-project').onclick = async () => {
    openModal.style.display = 'flex';
    try {
      const r = await api('GET', '/api/projects');
      const allProjects = r.projects || [];
      const openList = $('project-list-modal');
      openList.innerHTML = allProjects.length
        ? allProjects.map(p => {
          const isLegacy = p.legacy;
          const needsVideos = p.needs_videos;
          const cardClass = `project-card ${p.is_current ? 'active' : ''} ${isLegacy ? 'project-card-legacy' : ''}`;
          const legacyBadge = isLegacy
            ? `<span class="legacy-badge">${t('project.badge_legacy')}</span>`
            : (needsVideos ? `<span class="legacy-badge">${t('project.badge_no_videos')}</span>` : '');
          const migrateBtn = isLegacy
            ? `<button class="project-card-migrate" title="${t('project.migrate_title')}">${t('project.migrate')}</button>`
            : '';
          return `
          <div class="${cardClass}" data-name="${escapeHtml(p.name || '')}" data-project-dir="${escapeHtml(p.project_dir || p.input_dir || '')}" data-legacy="${isLegacy}" data-needs-videos="${needsVideos}">
            <div class="project-card-header">
              <span class="project-card-name">${escapeHtml(p.name)} ${p.is_current ? t('project.current_badge') : ''} ${legacyBadge}</span>
              <span class="project-card-actions">
                ${migrateBtn}
                <button class="project-card-remove" title="${t('project.remove_title')}">✕</button>
              </span>
            </div>
            <div class="project-card-meta">
              ${t('project.dir_label', { path: escapeHtml(p.project_dir || p.input_dir || '') })}<br>
              ${t('project.output_dir_label', { path: escapeHtml(p.output_dir || '') })}<br>
              ${t('project.steps_label', { steps: ['compress', 'analyze', 'scripts', 'plan', 'label', 'cut']
                .map(k => {
                  const label = t(`run.step_short.${k}`);
                  const done = p.steps?.[k];
                  const title = done ? t('project.step_done_title', { label }) : t('project.step_pending_title', { label });
                  return `<span class="step-dot${done ? ' done' : ''}" title="${title}">${label}</span>`;
                })
                .join(' ') })}
            </div>
          </div>`;
        }).join('')
        : `<p class="muted">${t('project.empty')}</p>`;
      function _setupRemoveBtn(card, name, projectDir) {
        const removeBtn = card.querySelector('.project-card-remove');
        if (!removeBtn) return;
        removeBtn.onclick = async (e) => {
          e.stopPropagation();
          if (!confirm(t('project.remove_confirm', { name }))) return;
          const r2 = await api('POST', '/api/project/remove', { project_dir: projectDir });
          if (r2.ok) {
            if (name === state.currentProject?.name && projectDir === state.currentProjectDir) {
              openModal.style.display = 'none';
              window.location.search = '';
            } else {
              $('btn-open-project').click();
            }
          }
        };
      }
      function _setupMigrateBtn(card, name, projectDir) {
        const btn = card.querySelector('.project-card-migrate');
        if (!btn) return;
        btn.onclick = async (e) => {
          e.stopPropagation();
          if (!confirm(t('project.migrate_confirm', { name }))) return;
          btn.disabled = true;
          btn.textContent = t('project.migrating');
          try {
            const r = await api('POST', '/api/project/migrate', { project_dir: projectDir });
            if (r.ok) {
              const message = r.migrated ? t('project.migrated', { name }) : (r.message || t('project.already_new'));
              setStatus(message, 'ok', { persist: false });
              const dest = r.project_dir || projectDir;
              await registerNotification({
                message,
                severity: 'success',
                title: t('status.operation_complete'),
                sourceType: 'ui_status',
                projectName: name,
                projectDir: dest,
              });
              if (confirm(t('project.migrate_confirm_open', { name }))) {
                openModal.style.display = 'none';
                window.location.search = `?project=${encodeURIComponent(name)}&project_dir=${encodeURIComponent(dest)}`;
                return;
              }
              $('btn-open-project').click();
            } else {
              setStatus(t('project.migrate_failed', { detail: r.error || t('common.unknown_error') }), 'err');
              btn.disabled = false;
              btn.textContent = t('project.migrate');
            }
          } catch (err) {
            setStatus(t('project.migrate_failed', { detail: err.message }), 'err');
            btn.disabled = false;
            btn.textContent = t('project.migrate');
          }
        };
      }
      openList.querySelectorAll('.project-card').forEach(card => {
        const name = card.dataset.name;
        const projectDir = card.dataset.projectDir;
        _setupRemoveBtn(card, name, projectDir);
        _setupMigrateBtn(card, name, projectDir);
        card.onclick = (e) => {
          if (e.target.closest('.project-card-remove') || e.target.closest('.project-card-migrate')) return;
          const isLegacy = card.dataset.legacy === 'true';
          if (isLegacy) {
            setStatus(t('project.legacy_hint'), 'warn');
            return;
          }
          if (projectDir === state.currentProjectDir) { openModal.style.display = 'none'; return; }
          if (state.dirty && !confirm(t('project.switch_confirm_dirty'))) return;
          openModal.style.display = 'none';
          window.location.search = `?project=${encodeURIComponent(name)}&project_dir=${encodeURIComponent(projectDir)}`;
        };
      });
    } catch (e) {
      const message = t('errors.project_list_failed', { detail: e.message });
      $('project-list-modal').innerHTML = '<p class="err">' + escapeHtml(message) + '</p>';
      setStatus(message, 'err');
    }
  };
  $('op-cancel').onclick = () => { openModal.style.display = 'none'; };
  openModal.querySelector('.modal-backdrop').onclick = null;
  $('op-open-path').onclick = async () => {
    const path = $('op-custom-path').value.trim();
    if (!path) { setStatus(t('project.need_dir'), 'warn'); return; }
    const openBtn = $('op-open-path');
    if (openBtn.disabled) return;
    openBtn.disabled = true;
    const prevLabel = openBtn.textContent;
    openBtn.textContent = t('project.opening');
    try {
      const r = await api('POST', '/api/project/add', { project_dir: path });
      if (r.ok) {
        openModal.style.display = 'none';
        const name = r.project?.name || path.split(/[\\/]/).pop();
        window.location.search = `?project=${encodeURIComponent(name)}&project_dir=${encodeURIComponent(path)}`;
      } else {
        setStatus(t('project.open_failed', { detail: r.error || t('common.unknown_error') }), 'err');
      }
    } catch (e) {
      setStatus(t('project.open_failed', { detail: e.message }), 'err');
    } finally {
      openBtn.disabled = false;
      openBtn.textContent = prevLabel;
    }
  };
  $('op-custom-path').onkeydown = (e) => {
    if (e.key === 'Enter') $('op-open-path').click();
  };

  // ---- Always register event handlers (before first async, so they work in empty state) ----
  $$('.source-toggle button').forEach(b => {
    b.onclick = () => setSource(b.dataset.source);
  });
  $$('.project-item').forEach(p => {
    p.onclick = (e) => {
      if (e.target.tagName === 'SELECT') return;
      if (p.dataset.entity === 'plan') selectPlan();
      else if (p.dataset.entity === 'run') selectRun();
      else if (p.dataset.entity === 'config') selectConfig();
      else if (p.dataset.entity === 'logs') selectLogs();
      else if (p.dataset.entity === 'tokens') selectTokens();
      else if (p.dataset.entity === 'tasks') selectTasks();
    };
  });
  document.body.addEventListener('click', async (e) => {
    const btn = e.target.closest('.browse-btn');
    if (!btn) return;
    e.preventDefault();
    const targetId = btn.dataset.target;
    const targetPath = btn.dataset.targetPath;
    const kind = btn.dataset.pickKind || btn.dataset.kind || 'folder';
    let inp = targetId ? document.getElementById(targetId) : null;
    try {
      let initial = (inp?.value || '').trim();
      if (targetPath) {
        const lookupInp = btn.parentElement?.querySelector(`input[data-path="${CSS.escape(targetPath)}"], textarea[data-path="${CSS.escape(targetPath)}"]`)
          || document.querySelector(`input[data-path="${CSS.escape(targetPath)}"], textarea[data-path="${CSS.escape(targetPath)}"]`);
        if (lookupInp) {
          inp = lookupInp;
          initial = (lookupInp.value || '').trim();
        }
      }
      let path;
      const pickerContext = {
        scope: btn.dataset.pickScope || 'project',
        projectDir: state.currentProjectDir || '',
      };
      if (kind === 'folder' || kind === 'directory') {
        path = await pickFolder(initial, pickerContext);
      } else {
        path = await pickFile(initial, kind, pickerContext);
      }
      applyPickToInput(inp, path);
    } catch (err) {
      console.error(err);
      addToast(t('shell.pick_failed', { detail: err.message || err }), 'error', 6000);
    }
  });
  $('btn-reload').onclick = async () => {
    try {
      const cur = state.currentVideo;
      await loadProject();
      await refreshFfmpegDepsUi();
      await loadVideos();
      if (cur && state.videos.find(x => x.file === cur)) {
        await selectVideo(cur);
      } else if (state.videos.length) {
        await selectVideo(state.videos[0].file);
      }
      setStatus(t('shell.reloaded'), 'ok');
    } catch (e) { setStatus(t('shell.reload_failed', { detail: e.message }), 'err'); }
  };
  async function revealProjectDir() {
    const path = state.currentProjectDir
      || state.currentProject?.project_dir
      || state.config?.project_dir
      || '';
    if (!path) {
      setStatus(t('project.no_dir'), 'warn');
      return;
    }
    try {
      const r = await api('POST', '/api/fs/reveal', { path });
      if (r.ok) {
        setStatus(t('shell.revealed', { path: r.path || path }), 'ok');
      } else {
        throw new Error(r.error || t('project.open_failed_bare'));
      }
    } catch (e) {
      setStatus(t('project.reveal_failed', { detail: e.message }), 'err');
    }
  }
  $('btn-reveal-project')?.addEventListener('click', revealProjectDir);
  document.getElementById('btn-theme').onclick = toggleTheme;
  initLanguageSwitcher();
  $('btn-save').onclick = save;
  document.getElementById('btn-select-videos').addEventListener('click', toggleSelection);
  document.getElementById('btn-add-videos').addEventListener('click', () => import('./sidebar-video-manage.js').then(m => m.openVideoManager()));
  const { initVideoFilterBar } = await import('./sidebar-data.js');
  initVideoFilterBar();
  const { initVideoDragSelect } = await import('./video-drag-select.js');
  initVideoDragSelect({
    enabled: () => state.selectionMode,
    selected: () => state.selectedFiles,
    commit: (files) => {
      state.selectedFiles = files;
      renderVideoList();
    },
  });
  const projectSwitcher = document.getElementById('btn-project-switcher');
  const projectMenu = document.getElementById('project-menu');
  const setProjectMenuOpen = (open) => {
    if (!projectMenu) return;
    projectMenu.hidden = !open;
    projectSwitcher?.setAttribute('aria-expanded', String(open));
  };
  projectSwitcher?.addEventListener('click', (e) => {
    e.stopPropagation();
    setProjectMenuOpen(projectMenu?.hidden !== false);
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.project-switcher')) setProjectMenuOpen(false);
  });
  projectMenu?.addEventListener('click', (e) => {
    if (e.target.closest('.project-menu-item')) {
      setProjectMenuOpen(false);
    }
  });
  // The callback parameter must not be named t: that shadows the i18n t()
  // imported above, so the dirty guard threw and the tab silently refused
  // to switch whenever there were unsaved edits.
  $$('.tab').forEach(tabButton => tabButton.onclick = () => {
    const toTab = tabButton.dataset.tab;
    if (shouldConfirmDirtyTabSwitch({ dirty: state.dirty, fromTab: state.currentTab, toTab })) {
      if (!confirm(t('shell.tab_dirty_confirm'))) return;
      clearDirty();
    }
    state.currentTab = toTab;
    renderActiveTab();
  });
  setupPlayer();
  document.addEventListener('keydown', (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (e.key === 's' && mod) { e.preventDefault(); save(); }
    if (e.key.toLowerCase() === 'o' && mod) {
      e.preventDefault();
      $('btn-open-project')?.click();
    }
    if (e.key === 'Escape') {
      // Close topmost open modal only (preserve stacked dialog state)
      const openModals = [...$$('.modal')].filter(m => m.style.display !== 'none');
      if (openModals.length) {
        const top = openModals[openModals.length - 1];
        if (top.id === 'modal-relink') {
          import('./sidebar-relink.js').then(m => m.closeRelinkModal());
        } else if (top.id === 'modal-video-manage') {
          import('./sidebar-video-manage.js').then(m => m.closeVideoManager());
        } else if (top.id === 'modal-batch-relink') {
          import('./sidebar-batch-relink.js').then(m => m.closeBatchRelinkModal());
        } else {
          top.style.display = 'none';
        }
      } else {
        setProjectMenuOpen(false);
      }
    }
    if (mod && e.key >= '1' && e.key <= '6') {
      e.preventDefault();
      const items = $$('.project-item');
      const idx = parseInt(e.key) - 1;
      if (idx < items.length) items[idx].click();
    }
  });
  window.addEventListener('beforeunload', (e) => {
    if (state.dirty) { e.preventDefault(); e.returnValue = t('shell.unsaved_changes'); }
  });
  const rerunClose = $('rerun-close');
  if (rerunClose) rerunClose.onclick = hideRerunProgress;
  // Wire up auth modal
  document.getElementById('auth-submit')?.addEventListener('click', submitToken);
  document.getElementById('auth-token-input')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') submitToken();
  });
  // ---- End event handlers ----

  try {
    await loadProjects();
    // 检查上次使用的项目是否为旧版
    function _isLastProjectLegacy() {
      if (!state.lastProject || !state.projects) return false;
      return state.projects.some(p => p.name === state.lastProject && p.legacy);
    }
    // 如果没有 URL 指定项目，也没有上次使用的项目，显示打开界面
    if (!urlProject && !state.lastProject && !state.currentProject) {
      hideVideoListLoading();
      $('proj-name').textContent = t('shell.select_project');
      $('btn-open-project').click();
      setStatus(t('project.none_loaded'), 'warn');
      return;
    }
    // 如果上次使用的项目是旧版，跳过自动跳转，让用户新建项目
    if (!urlProject && !urlProjectDir && _isLastProjectLegacy()) {
      hideVideoListLoading();
      state.lastProject = null;
      $('proj-name').textContent = t('shell.select_project');
      $('btn-open-project').click();
      setStatus(t('project.last_legacy'), 'warn');
      return;
    }
    // 如果 URL 没有指定项目，但有上次使用的项目，自动跳转
    if (!urlProject && !urlProjectDir && state.lastProject && state.lastProject !== state.currentProject?.name) {
      const lastDir = state.lastProjectDir
        || state.projects.find(p => p.name === state.lastProject)?.project_dir
        || '';
      const projectDirParam = lastDir ? `&project_dir=${encodeURIComponent(lastDir)}` : '';
      window.location.search = `?project=${encodeURIComponent(state.lastProject)}${projectDirParam}`;
      return;
    }
    // 如果 URL 指定了项目，但当前不是该项目，需要重载
    const urlProjectObj = state.projects?.find(p => p.name === urlProject || p.project_dir === urlProjectDir);
    if (urlProjectObj?.legacy) {
      hideVideoListLoading();
      state.currentProjectName = null;
      state.currentProjectDir = null;
      $('proj-name').textContent = t('shell.select_project');
      $('btn-open-project').click();
      setStatus(t('project.url_legacy'), 'warn');
      return;
    }
    if (urlProjectDir && (!state.currentProject || state.currentProjectDir !== urlProjectDir)) {
      state.currentProjectName = urlProject;
      state.currentProjectDir = urlProjectDir;
    } else if (urlProject && (!state.currentProject || state.currentProject.name !== urlProject)) {
      state.currentProjectName = urlProject;
    }
    await loadConfig();
    await initI18n(state.config?.ui?.language || readStoredLocale());
    await loadFfmpegDeps();
    await refreshRuntimeWarningsBanner();
    await loadProject();
    if (urlDay) state.currentDay = urlDay;
    // 检查项目是否缺少 project.yaml，提示用户创建
    (async () => {
      if (!state.currentProjectName) return;
      try {
        const check = await api('GET', '/api/config/raw');
        if (check.needs_init) {
          // 在状态栏显示可操作的提示
          const el = $('status');
          el.innerHTML = `${t('project.needs_init_prefix')}<button id="btn-create-project-config" style="background:none;border:1px solid currentColor;border-radius:3px;padding:2px 8px;cursor:pointer;color:inherit;font:inherit">${t('project.needs_init_action')}</button>`;
          el.className = 'status warn';
          const createBtn = $('btn-create-project-config');
          if (createBtn) createBtn.onclick = initProjectConfig;
        }
      } catch {
        // ignore
      }
    })();
    await loadPlans();
    // 自动选择第一个可用 plan（如果有）
    if (state.availablePlans.length) {
      // 如果 project 指定的 day 有对应 plan 则保留, 否则用第一个
      const hasDay = state.availablePlans.some(p => p.day_label === state.currentDay);
      if (!hasDay) state.currentDay = state.availablePlans[0].day_label;
      updateSidebarDay();
      try { state.plan = await api('GET', `/api/plan?day=${state.currentDay}`); }
      catch (e) { /* ignore */ }
    }
    await loadVideos();
    const restore = resolveSessionRestore({
      lastEntity: urlEntity || state.lastEntity,
      lastVideo: urlVideo || state.lastVideo,
      videos: state.videos,
    });
    if (restore.entity === 'plan') {
      await selectPlan();
      if (restore.video) {
        // keep plan entity but ensure a player source is available when possible
        const v = state.videos.find(x => x.file === restore.video);
        if (v && !v.missing) {
          state.currentVideo = restore.video;
        }
      }
    } else if (restore.entity === 'run') {
      await selectRun();
    } else if (restore.entity === 'config') {
      await selectConfig();
    } else if (restore.entity === 'logs') {
      await selectLogs();
    } else if (restore.entity === 'tokens') {
      await selectTokens();
    } else if (restore.entity === 'tasks') {
      await selectTasks();
      if (urlTaskId) {
        const tasks = await import('./task-center.js');
        await tasks.selectTask(urlTaskId);
      }
    } else if (restore.video) {
      await selectVideo(restore.video);
    } else if (state.videos.length) {
      await selectVideo(state.videos[0].file);
    } else {
      setStatus(t('project.no_videos'), 'warn');
      renderVideoList();
    }
    if (urlEntity || urlVideo || urlDay || urlTaskId) {
      const cleaned = stripQueryParams(window.location.search, ['entity', 'video', 'day', 'task_id']);
      window.history.replaceState(null, '', window.location.pathname + cleaned + (window.location.hash || ''));
    }
    startTaskStream();
    void loadTasks();
  } catch (e) {
    hideVideoListLoading();
    $('proj-name').textContent = t('shell.load_failed');
    setStatus(t('shell.init_failed', { detail: e.message }), 'err');
  }
}

init();
