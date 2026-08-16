import { state } from './state.js';
import { $, $$, escapeHtml, setStatus, updateSidebarDay, clearDirty } from './utils.js';
import { api, submitToken } from './api.js';
import { initLayout } from './layout.js';
import { initTheme, toggleTheme } from './theme.js';
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
import { resolveSessionRestore } from './session-restore.js';
import { shouldConfirmDirtyTabSwitch } from './editor-save.js';
import { stripQueryParams } from './url-params.js';

// Expose functions referenced by inline onclick handlers in HTML
window.switchToOriginalThenCompress = switchToOriginalThenCompress;
window.goToRunTab = goToRunTab;
window.initProjectConfig = initProjectConfig;
window.refineCurrentFile = refineCurrentFile;
window.addToast = addToast;

let _orphanedCutBackups = [];

async function refreshRuntimeWarningsBanner() {
  let missingKeys = null;
  try {
    const r = await api('GET', '/api/deps/keys');
    missingKeys = r.missing || [];
  } catch {
    missingKeys = null;
  }
  try {
    const r = await api('GET', '/api/cut/orphaned-backups');
    _orphanedCutBackups = r.items || [];
  } catch {
    _orphanedCutBackups = [];
  }
  updateRuntimeWarnings(state.config, {
    orphanedCutBackups: _orphanedCutBackups,
    ffmpegDeps: state.deps,
    missingKeys,
    onAction: handleRuntimeWarningAction,
  });
}

/** Re-probe ffmpeg, refresh banner (keeps orphan warnings), re-render menus. */
async function refreshFfmpegDepsUi() {
  await loadFfmpegDeps();
  await refreshRuntimeWarningsBanner();
  renderVideoList();
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
  if (actionId !== 'restore-cut-backups') return;
  if (!_orphanedCutBackups.length) {
    setStatus('没有可恢复的裁剪备份', 'warn');
    return;
  }
  const n = _orphanedCutBackups.length;
  const conflicts = (_orphanedCutBackups || []).filter((it) => it.conflict === 'true' || it.conflict === true);
  if (!confirm(`将恢复 ${n} 个中断覆盖前的旧裁剪文件（还原 *.clio_bak）。继续？`)) {
    return;
  }
  try {
    let body = {};
    if (conflicts.length) {
      const keepNew = confirm(
        `${conflicts.length} 个文件的目标与备份共存（新成片已生成）。保留新版并删除备份？\n选择「取消」则恢复旧版并删除新版。`,
      );
      body = { keep_target: keepNew };
    }
    const r = await api('POST', '/api/cut/restore-backups', body);
    const count = r.count || (r.restored || []).length || 0;
    const errN = (r.errors || []).length;
    setStatus(
      errN ? `已恢复 ${count} 个，${errN} 个失败` : `已处理 ${count} 个裁剪备份`,
      errN ? 'warn' : 'ok',
    );
    addToast(errN ? `处理 ${count}，失败 ${errN}` : `已处理 ${count} 个旧文件`, errN ? 'warning' : 'success');
  } catch (e) {
    if (e.status === 409 && e.body && e.body.conflicts && e.body.conflicts.length) {
      const keepNew = confirm(
        `${e.body.conflicts.length} 个文件的目标与备份共存（新成片已生成）。保留新版并删除备份？\n选择「取消」则恢复旧版并删除新版。`,
      );
      try {
        const r = await api('POST', '/api/cut/restore-backups', { keep_target: keepNew });
        const count = r.count || (r.restored || []).length || 0;
        setStatus(`已处理 ${count} 个裁剪备份`, 'ok');
        addToast(`已处理 ${count} 个旧文件`, 'success');
      } catch (e2) {
        setStatus('恢复失败: ' + e2.message, 'err');
        addToast('恢复失败: ' + e2.message, 'error', 6000);
      }
    } else {
      setStatus('恢复失败: ' + e.message, 'err');
      addToast('恢复失败: ' + e.message, 'error', 6000);
    }
  }
  await refreshRuntimeWarningsBanner();
}

/** Show an in-app modal with ffmpeg/ffprobe install guidance (B-2). */
function showFfmpegHelp() {
  const backdrop = document.createElement('div');
  backdrop.className = 'modal';
  backdrop.style.display = 'flex';
  const steps = [
    '下载 Windows 版 ffmpeg（含 ffprobe）：gyan.dev 或 BtbN 的 release 构建，或用包管理器（winget install Gyan.FFmpeg / choco install ffmpeg）。',
    '解压到本地目录（例如 C:\\ffmpeg），记下其中的 bin 文件夹路径。',
    '把 bin 目录加入系统 PATH；或打开 设置 → 全局，在 paths.ffmpeg / paths.ffprobe 填完整路径。',
    '配置完成后，点击下方「重新检测」立即生效。',
  ].map((s, i) => `<li style="margin:6px 0">${i + 1}. ${s}</li>`).join('');
  backdrop.innerHTML = `
    <div class="modal-backdrop"></div>
    <div class="modal-dialog" style="max-width:560px">
      <h3 style="margin:0 0 var(--space-4)">安装 ffmpeg / ffprobe</h3>
      <p class="muted" style="margin:0 0 12px">压缩、波形、裁剪、转录抽音都依赖这两个命令行工具。当前应用内未检测到。</p>
      <ol style="margin:0 0 16px;padding-left:0;list-style:none;font-size:var(--text-sm)">
        ${steps}
      </ol>
      <p class="muted" style="font-size:var(--text-xs);margin:0">打包版 README（README-desktop.md，与 clio.exe 同目录）也包含完整说明。</p>
      <div class="modal-actions" style="margin-top:16px">
        <a href="https://www.gyan.dev/ffmpeg/builds/" target="_blank" rel="noopener" class="btn-secondary" style="text-decoration:none">打开下载页</a>
        <button id="ffmpeg-help-reprobe" class="btn-primary">重新检测</button>
        <button id="ffmpeg-help-close" class="btn-secondary">关闭</button>
      </div>
    </div>
  `;
  document.body.appendChild(backdrop);
  const close = () => backdrop.remove();
  backdrop.querySelector('.modal-backdrop').onclick = close;
  backdrop.querySelector('#ffmpeg-help-close').onclick = close;
  backdrop.querySelector('#ffmpeg-help-reprobe').onclick = async () => {
    await refreshFfmpegDepsUi();
    close();
  };
}

async function init() {
  initLayout();
  initTheme();
  initDesktopPickers(document);

  // 从 URL 读取 project + project_dir 参数
  const urlParams = new URLSearchParams(window.location.search);
  const urlProject = urlParams.get('project');
  const urlProjectDir = urlParams.get('project_dir');
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

  // 新建/打开项目模态框（必须在 try 之前绑定，空状态时也会用到）
  const newModal = $('modal-new-project');
  $('btn-new-project').onclick = () => { newModal.style.display = 'flex'; };
  $('np-cancel').onclick = () => { newModal.style.display = 'none'; };
  newModal.querySelector('.modal-backdrop').onclick = null;
  $('np-create').onclick = async () => {
    const name = $('np-name').value.trim();
    const projectDir = $('np-input-dir').value.trim();
    const outputDir = $('np-output-dir').value.trim();
    if (!name || !projectDir) { setStatus('请填写项目名称和项目目录', 'warn'); return; }
    const createBtn = $('np-create');
    if (createBtn.disabled) return;
    createBtn.disabled = true;
    const prevLabel = createBtn.textContent;
    createBtn.textContent = '创建中...';
    try {
      const body = { name, project_dir: projectDir };
      if (outputDir) body.output_dir = outputDir;
      const r = await api('POST', '/api/project/create', body);
      if (r.ok) {
        newModal.style.display = 'none';
        window.location.search = `?project=${encodeURIComponent(name)}&project_dir=${encodeURIComponent(projectDir)}`;
      } else {
        setStatus('创建失败: ' + (r.error || '未知错误'), 'err');
      }
    } catch (e) {
      setStatus('创建失败: ' + e.message, 'err');
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
            ? '<span class="legacy-badge">需迁移</span>'
            : (needsVideos ? '<span class="legacy-badge">无视频</span>' : '');
          const migrateBtn = isLegacy
            ? '<button class="project-card-migrate" title="迁移到新结构 (videos.json)">迁移</button>'
            : '';
          return `
          <div class="${cardClass}" data-name="${escapeHtml(p.name || '')}" data-project-dir="${escapeHtml(p.project_dir || p.input_dir || '')}" data-legacy="${isLegacy}" data-needs-videos="${needsVideos}">
            <div class="project-card-header">
              <span class="project-card-name">${escapeHtml(p.name)} ${p.is_current ? '(当前)' : ''} ${legacyBadge}</span>
              <span class="project-card-actions">
                ${migrateBtn}
                <button class="project-card-remove" title="从项目列表移除">✕</button>
              </span>
            </div>
            <div class="project-card-meta">
              项目目录: ${escapeHtml(p.project_dir || p.input_dir || '')}<br>
              输出目录: ${escapeHtml(p.output_dir || '')}<br>
              步骤: ${[['compress','压缩'],['analyze','分析'],['scripts','口播'],['plan','规划'],['label','标号'],['cut','裁剪']]
                .map(([k,l]) => p.steps?.[k] ? `<span class="step-dot done" title="${l} 完成">${l}</span>` : `<span class="step-dot" title="${l} 待完成">${l}</span>`)
                .join(' ')}
            </div>
          </div>`;
        }).join('')
        : '<p class="muted">还没有项目，请先新建一个。</p>';
      function _setupRemoveBtn(card, name, projectDir) {
        const removeBtn = card.querySelector('.project-card-remove');
        if (!removeBtn) return;
        removeBtn.onclick = async (e) => {
          e.stopPropagation();
          if (!confirm(`从项目列表移除「${name}」？`)) return;
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
          if (!confirm(`迁移项目 "${name}" 到新结构？\n将生成 videos.json 并移除 paths.input_dir。`)) return;
          btn.disabled = true;
          btn.textContent = '迁移中...';
          try {
            const r = await api('POST', '/api/project/migrate', { project_dir: projectDir });
            if (r.ok) {
              setStatus(r.migrated ? `已迁移: ${name}` : (r.message || '已是新结构'), 'ok');
              const dest = r.project_dir || projectDir;
              if (confirm(`迁移完成。是否立即打开项目「${name}」？`)) {
                openModal.style.display = 'none';
                window.location.search = `?project=${encodeURIComponent(name)}&project_dir=${encodeURIComponent(dest)}`;
                return;
              }
              $('btn-open-project').click();
            } else {
              setStatus('迁移失败: ' + (r.error || 'unknown'), 'err');
              btn.disabled = false;
              btn.textContent = '迁移';
            }
          } catch (err) {
            setStatus('迁移失败: ' + err.message, 'err');
            btn.disabled = false;
            btn.textContent = '迁移';
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
            setStatus('该项目仍含 paths.input_dir，请先点击「迁移」或运行 python main.py migrate', 'warn');
            return;
          }
          if (projectDir === state.currentProjectDir) { openModal.style.display = 'none'; return; }
          if (state.dirty && !confirm('切换项目？未保存的修改将丢失。')) return;
          openModal.style.display = 'none';
          window.location.search = `?project=${encodeURIComponent(name)}&project_dir=${encodeURIComponent(projectDir)}`;
        };
      });
    } catch (e) {
      $('project-list-modal').innerHTML = '<p class="err">加载项目列表失败: ' + escapeHtml(e.message) + '</p>';
    }
  };
  $('op-cancel').onclick = () => { openModal.style.display = 'none'; };
  openModal.querySelector('.modal-backdrop').onclick = null;
  $('op-open-path').onclick = async () => {
    const path = $('op-custom-path').value.trim();
    if (!path) { setStatus('请输入项目目录路径', 'warn'); return; }
    const openBtn = $('op-open-path');
    if (openBtn.disabled) return;
    openBtn.disabled = true;
    const prevLabel = openBtn.textContent;
    openBtn.textContent = '打开中...';
    try {
      const r = await api('POST', '/api/project/add', { project_dir: path });
      if (r.ok) {
        openModal.style.display = 'none';
        const name = r.project?.name || path.split(/[\\/]/).pop();
        window.location.search = `?project=${encodeURIComponent(name)}&project_dir=${encodeURIComponent(path)}`;
      } else {
        setStatus('打开失败: ' + (r.error || '未知错误'), 'err');
      }
    } catch (e) {
      setStatus('打开失败: ' + e.message, 'err');
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
      addToast('选择失败: ' + (err.message || err), 'error', 6000);
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
      setStatus('已重新加载', 'ok');
    } catch (e) { setStatus('重载失败: ' + e.message, 'err'); }
  };
  async function revealProjectDir() {
    const path = state.currentProjectDir
      || state.currentProject?.project_dir
      || state.config?.project_dir
      || '';
    if (!path) {
      setStatus('当前没有项目目录', 'warn');
      return;
    }
    try {
      const r = await api('POST', '/api/fs/reveal', { path });
      if (r.ok) {
        setStatus(`已打开: ${r.path || path}`, 'ok');
      } else {
        throw new Error(r.error || '打开失败');
      }
    } catch (e) {
      setStatus('打开目录失败: ' + e.message, 'err');
    }
  }
  $('btn-reveal-project')?.addEventListener('click', revealProjectDir);
  document.getElementById('btn-theme').onclick = toggleTheme;
  $('btn-save').onclick = save;
  document.getElementById('btn-select-videos').addEventListener('click', toggleSelection);
  document.getElementById('btn-add-videos').addEventListener('click', () => import('./sidebar-video-manage.js').then(m => m.openVideoManager()));
  const { initVideoFilterBar } = await import('./sidebar-data.js');
  initVideoFilterBar();
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
  $$('.tab').forEach(t => t.onclick = () => {
    const toTab = t.dataset.tab;
    if (shouldConfirmDirtyTabSwitch({ dirty: state.dirty, fromTab: state.currentTab, toTab })) {
      if (!confirm('当前 tab 有未保存的修改，确定切换吗？')) return;
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
    if (state.dirty) { e.preventDefault(); e.returnValue = '有未保存的修改'; }
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
      $('proj-name').textContent = '选择项目';
      $('btn-open-project').click();
      setStatus('尚未加载项目，请选择一个项目开始。', 'warn');
      return;
    }
    // 如果上次使用的项目是旧版，跳过自动跳转，让用户新建项目
    if (!urlProject && !urlProjectDir && _isLastProjectLegacy()) {
      state.lastProject = null;
      $('proj-name').textContent = '选择项目';
      $('btn-open-project').click();
      setStatus('上次使用的项目是旧版，请新建项目', 'warn');
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
      state.currentProjectName = null;
      state.currentProjectDir = null;
      $('proj-name').textContent = '选择项目';
      $('btn-open-project').click();
      setStatus('URL 指向的是旧版项目，不支持打开，请新建项目', 'warn');
      return;
    }
    if (urlProjectDir && (!state.currentProject || state.currentProjectDir !== urlProjectDir)) {
      state.currentProjectName = urlProject;
      state.currentProjectDir = urlProjectDir;
    } else if (urlProject && (!state.currentProject || state.currentProject.name !== urlProject)) {
      state.currentProjectName = urlProject;
    }
    await loadConfig();
    await loadFfmpegDeps();
    await refreshRuntimeWarningsBanner();
    await loadProject();
    // 检查项目是否缺少 project.yaml，提示用户创建
    (async () => {
      if (!state.currentProjectName) return;
      try {
        const check = await api('GET', '/api/config/raw');
        if (check.needs_init) {
          // 在状态栏显示可操作的提示
          const el = $('status');
          el.innerHTML = `Project has no project.yaml, <button id="btn-create-project-config" style="background:none;border:1px solid currentColor;border-radius:3px;padding:2px 8px;cursor:pointer;color:inherit;font:inherit">create one now</button>`;
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
      lastEntity: state.lastEntity,
      lastVideo: state.lastVideo,
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
    } else if (restore.video) {
      await selectVideo(restore.video);
    } else if (state.videos.length) {
      await selectVideo(state.videos[0].file);
    } else {
      setStatus('项目目录中暂无视频文件', 'warn');
      renderVideoList();
    }
  } catch (e) {
    $('proj-name').textContent = '(加载失败)';
    setStatus('Init failed: ' + e.message, 'err');
  }
}

init();
