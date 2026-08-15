import { state } from './state.js';
import {
  $, $$, setStatus, fmtTime,
  updateSidebarDay, updateEntityUI, clearDirty,
} from './utils.js';
import { api } from './api.js';
import { seekToGlobal, stopPreview } from './viewer.js';
import { loadWaveformForCurrentVideo } from './waveform.js';
import { showRerunProgress, hideRerunProgress } from './sidebar-rerun.js';
import { openVideoManager } from './sidebar-video-manage.js';
import {
  loadProjects, loadConfig, loadFfmpegDeps, loadPlans, loadProject, loadVideos, saveProject,
  updateSelectBtnVisibility, renderVideoList,
} from './sidebar-data.js';
import { selectVideosButtonHtml } from './select-btn.js';

let _selectVideoRequestId = 0;

// ── Selection ──────────────────────────────────────────────────

async function selectVideo(file, options = {}) {
  if (state.dirty) {
    if (!confirm('当前 tab 有未保存的修改，确定切换视频吗？')) return;
  }
  if (state.previewActive) stopPreview();
  $('player-pane').classList.remove('plan-mode');
  state.currentEntity = 'video';
  state.currentVideo = file;
  clearDirty();
  state.texts = null;
  state.voiceover = null;
  state.transcript = null;
  state._refineError = null;
  const requestId = ++_selectVideoRequestId;

  const v = state.videos.find(x => x.file === file);
  if (!v) return;

  const player = $('player');
  const projParam = state.currentProjectName ? `&project=${encodeURIComponent(state.currentProjectName)}` : '';
  const tokenParam = sessionStorage.getItem('api_token');
  const extraParam = tokenParam ? `&token=${encodeURIComponent(tokenParam)}` : '';
  const absParam = v.abs_path ? `&abspath=${encodeURIComponent(v.abs_path)}` : '';
  player.src = `/api/video?file=${encodeURIComponent(file)}&source=${state.source}${absParam}${projParam}${extraParam}`;
  $('player-name').textContent = file;

  player.onloadedmetadata = () => {
    $('player-time').textContent = `${fmtTime(0)} / ${fmtTime(player.duration)}`;
    const defaultSeek = state.source === 'original' ? (v.offset_sec || 0) : 0;
    const requestedSeek = Number.isFinite(options.seekSec) ? options.seekSec : defaultSeek;
    const seekSec = Number.isFinite(player.duration) && player.duration > 0
      ? Math.min(Math.max(0, requestedSeek), player.duration)
      : Math.max(0, requestedSeek);
    player.currentTime = seekSec;
    if (options.play) player.play().catch(() => {});
  };
  loadWaveformForCurrentVideo();

  const loadArtifact = async (url, label) => {
    if (!url) return null;
    try { return await api('GET', url); }
    catch (e) {
      if (requestId === _selectVideoRequestId && state.currentVideo === file) {
        setStatus(`${label} 加载失败: ${e.message}`, 'err');
      }
      return null;
    }
  };
  const [texts, voiceover, transcript, plan] = await Promise.all([
    loadArtifact(v.text_json ? `/api/texts?file=${encodeURIComponent(v.text_json)}` : null, 'texts'),
    loadArtifact(v.script_json ? `/api/voiceover?file=${encodeURIComponent(v.script_json)}` : null, 'voiceover'),
    loadArtifact(v.transcript_file ? `/api/transcripts?video=${encodeURIComponent(v.file)}` : null, 'transcript'),
    state.plan ? Promise.resolve(state.plan) : loadArtifact(`/api/plan?day=${state.currentDay}`, 'plan'),
  ]);
  if (requestId !== _selectVideoRequestId || state.currentVideo !== file) return;
  state.texts = texts;
  state.voiceover = voiceover;
  state.transcript = transcript;
  if (!state.plan && plan) state.plan = plan;

  renderVideoList();
  import('./editor.js').then(mod => mod.renderActiveTab());
  updateEntityUI();
  saveProject();
}

async function selectPlan(dayOverride) {
  if (state.dirty) {
    if (!confirm('当前 tab 有未保存的修改，确定切换到规划吗？')) return;
  }
  if (state.previewActive) stopPreview();
  $('player-pane').classList.add('plan-mode');
  state.currentEntity = 'plan';
  clearDirty();
  if (dayOverride) state.currentDay = dayOverride;
  // Keep run SSE alive across entity switches so done/cancel handlers still fire.
  try { state.plan = await api('GET', `/api/plan?day=${state.currentDay}`); }
  catch (e) { state.plan = null; }
  updateSidebarDay();
  updateEntityUI();
  updateSelectBtnVisibility();
  import('./editor.js').then(mod => mod.renderActiveTab());
  saveProject();
}

async function selectRun() {
  if (state.dirty) {
    if (!confirm('当前 tab 有未保存的修改，确定切换到运行吗？')) return;
  }
  if (state.previewActive) stopPreview();
  $('player-pane').classList.remove('plan-mode');
  state.currentEntity = 'run';
  clearDirty();
  updateEntityUI();
  updateSelectBtnVisibility();
  import('./editor.js').then(mod => mod.renderActiveTab());
  saveProject();
}

async function selectConfig() {
  if (state.dirty) {
    if (!confirm('当前 tab 有未保存的修改，确定切换到设置吗？')) return;
  }
  if (state.previewActive) stopPreview();
  $('player-pane').classList.remove('plan-mode');
  state.currentEntity = 'config';
  clearDirty();
  try {
    const [raw, global, project] = await Promise.all([
      api('GET', '/api/config/raw'),
      api('GET', '/api/config/global'),
      api('GET', '/api/config/project'),
    ]);
    if (raw.needs_init || project.needs_init) {
      state.configRaw = null;
      state.configGlobal = global || {};
      state.configProject = null;
      state._needsConfigInit = true;
    } else {
      state.configRaw = raw;
      state.configGlobal = global || {};
      state.configProject = project || {};
      state._needsConfigInit = false;
    }
  } catch (e) {
    setStatus('配置加载失败: ' + e.message, 'err');
    state.configRaw = {};
    state.configGlobal = {};
    state.configProject = {};
    state._needsConfigInit = false;
  }
  updateEntityUI();
  updateSelectBtnVisibility();
  import('./editor.js').then(mod => mod.renderActiveTab());
  saveProject();
}

async function selectLogs() {
  if (state.dirty) {
    if (!confirm('当前 tab 有未保存的修改，确定切换到日志吗？')) return;
  }
  if (state.previewActive) stopPreview();
  $('player-pane').classList.remove('plan-mode');
  state.currentEntity = 'logs';
  clearDirty();
  updateEntityUI();
  updateSelectBtnVisibility();
  import('./editor.js').then(mod => mod.renderActiveTab());
  saveProject();
}

async function selectTokens() {
  if (state.dirty) {
    if (!confirm('当前 tab 有未保存的修改，确定切换到统计吗？')) return;
  }
  if (state.previewActive) stopPreview();
  $('player-pane').classList.remove('plan-mode');
  state.currentEntity = 'tokens';
  clearDirty();
  updateEntityUI();
  updateSelectBtnVisibility();
  import('./editor.js').then(mod => mod.renderActiveTab());
  saveProject();
}

function toggleSelection() {
  state.selectionMode = !state.selectionMode;
  if (!state.selectionMode) {
    state.selectedFiles = [];
  }
  renderVideoList();
  const btn = document.getElementById('btn-select-videos');
  if (btn) {
    btn.innerHTML = selectVideosButtonHtml(state.selectionMode);
    btn.style.border = state.selectionMode ? '1px solid var(--warn)' : '';
  }
}

function sameVideoIndex(a, b) {
  if (a === undefined || a === null || b === undefined || b === null) return false;
  const left = String(a).trim();
  const right = String(b).trim();
  if (!left || !right) return false;
  if (left === right) return true;
  const leftNum = Number.parseInt(left, 10);
  const rightNum = Number.parseInt(right, 10);
  return Number.isFinite(leftNum) && Number.isFinite(rightNum) && leftNum === rightNum;
}

async function selectTasks() {
  if (state.dirty) {
    if (!confirm('当前 tab 有未保存的修改，确定切换到任务中心吗？')) return;
  }
  if (state.previewActive) stopPreview();
  $('player-pane').classList.remove('plan-mode');
  state.currentEntity = 'tasks';
  clearDirty();
  updateEntityUI();
  updateSelectBtnVisibility();
  const mod = await import('./task-center.js');
  mod.renderTasks();
  saveProject();
}

function _sourceSwitchSeekTime(currentTime, fromSource, oldVideo, toSource, targetVideo) {
  const time = Number.isFinite(currentTime) ? currentTime : 0;
  const oldOffset = fromSource === 'original' ? Number(oldVideo?.offset_sec) || 0 : 0;
  const targetOffset = toSource === 'original' ? Number(targetVideo?.offset_sec) || 0 : 0;
  return Math.max(0, time - oldOffset) + targetOffset;
}

function _sourceSwitchResumePoint(
  wasPlanView,
  currentTime,
  globalSec,
  fromSource,
  oldVideo,
  toSource,
  targetVideo,
) {
  if (wasPlanView) {
    return { globalSec: Number.isFinite(globalSec) ? Math.max(0, globalSec) : 0 };
  }
  return {
    seekSec: _sourceSwitchSeekTime(currentTime, fromSource, oldVideo, toSource, targetVideo),
  };
}

async function setSource(source, options = {}) {
  if (source === state.source) return;
  if (state.dirty) {
    if (!confirm('当前 tab 有未保存的修改，确定切换源吗？')) return;
  }
  const player = $('player');
  const currentTime = Number.isFinite(player?.currentTime) ? player.currentTime : 0;
  const wasPlaying = Boolean(player && !player.paused && !player.ended);
  const oldSource = state.source;
  const oldVideo = options.fromVideo || state.videos.find(x => x.file === state.currentVideo);
  const oldMatchFile = options.matchFile ?? oldVideo?.match?.file;
  const oldMatchAbsPath = options.matchAbsPath ?? oldVideo?.match?.abs_path ?? null;
  const wasPlanView = state.currentEntity === 'plan';
  const globalSec = Number.isFinite(state.previewGlobalSec) ? state.previewGlobalSec : 0;
  if (state.previewActive) stopPreview();
  if (!wasPlanView) {
    $('player-pane').classList.remove('plan-mode');
  }
  state.source = source;
  state.currentVideo = null;
  state.selectionMode = false;
  state.selectedFiles = [];
  state.videoFilter = { q: '', stage: '', mode: 'missing' };
  if ($('video-filter-input')) $('video-filter-input').value = '';
  state.texts = null;
  state.voiceover = null;
  $$('.source-toggle button').forEach(b => b.classList.toggle('active', b.dataset.source === source));
  saveProject();
  try {
    await loadVideos();
    const target = _findSourceSwitchTarget(oldVideo, state.videos, oldMatchFile, oldMatchAbsPath);
    if (state.videos.length) {
      if (state.currentEntity === 'plan') {
        import('./editor.js').then(mod => mod.renderActiveTab());
        if (target) {
          if (target.missing) {
            $('player').removeAttribute('src');
            $('player-name').textContent = '对应原视频当前离线';
            setStatus(`已切换到${source}视图（对应原视频离线）`, 'warn');
          } else {
            const resume = _sourceSwitchResumePoint(
              true,
              currentTime,
              globalSec,
              oldSource,
              oldVideo,
              source,
              target,
            );
            seekToGlobal(resume.globalSec, { play: wasPlaying });
            setStatus(`已切换到${source}视图`, 'ok');
          }
        } else {
          $('player').removeAttribute('src');
          $('player-name').textContent = '当前规划段在此源无对应视频';
          setStatus(`已切换到${source}视图（无对应视频）`, 'ok');
        }
      } else {
        if (target?.missing) {
          setStatus('对应原视频当前离线，已切换视图', 'warn');
          state.currentVideo = target.file;
          renderVideoList();
          saveProject();
        } else {
          const nextVideo = target || state.videos[0];
          if (target) {
            const resume = _sourceSwitchResumePoint(
              false,
              currentTime,
              globalSec,
              oldSource,
              oldVideo,
              source,
              nextVideo,
            );
            await selectVideo(nextVideo.file, { seekSec: resume.seekSec, play: wasPlaying });
          } else {
            await selectVideo(nextVideo.file);
          }
        }
      }
    } else {
      $('player').removeAttribute('src');
      $('player-name').textContent = '当前视图没有视频';
      setStatus(`当前视图没有视频 (${source})`, 'warn');
    }
  } catch (e) {
    setStatus('切换源失败: ' + e.message, 'err');
  }
}

function _findSourceSwitchTarget(oldVideo, videos, oldMatchFile = null, oldMatchAbsPath = null) {
  if (!oldVideo) return null;
  const norm = (p) => String(p || '').replace(/\\/g, '/').toLowerCase();
  const abs = oldMatchAbsPath ? norm(oldMatchAbsPath) : '';
  return videos.find(v =>
    (abs && norm(v.abs_path) === abs)
    || v.file === oldMatchFile
    || v.match?.file === oldVideo.file
    || (oldVideo.index && v.index === oldVideo.index)
  ) || null;
}

async function jumpToCounterpart(video) {
  if (!video?.match?.source) return;
  if (video.match.missing) {
    setStatus('对应原视频当前离线或不存在', 'warn');
    return;
  }
  if (video.match.source === state.source) {
    await selectVideo(video.match.file);
    return;
  }
  await setSource(video.match.source, {
    fromVideo: video,
    matchFile: video.match.file,
    matchAbsPath: video.match.abs_path || null,
  });
}

async function switchToOriginalThenCompress() {
  await setSource('original');
}

function goToRunTab() {
  // Same dirty guard as selectRun — empty CTAs should not discard edits silently
  if (state.dirty) {
    if (!confirm('当前 tab 有未保存的修改，确定切换到运行吗？')) return;
  }
  if (state.previewActive) stopPreview();
  $('player-pane').classList.remove('plan-mode');
  state.currentEntity = 'run';
  clearDirty();
  updateEntityUI();
  updateSelectBtnVisibility();
  import('./editor.js').then(mod => mod.renderActiveTab());
  saveProject();
}

export {
  loadProjects,
  loadConfig,
  loadFfmpegDeps,
  loadPlans,
  loadProject,
  loadVideos,
  saveProject,
  renderVideoList,
  selectVideo,
  selectPlan,
  selectRun,
  selectConfig,
  selectLogs,
  selectTokens,
  selectTasks,
  setSource,
  jumpToCounterpart,
  _findSourceSwitchTarget,
  _sourceSwitchSeekTime,
  _sourceSwitchResumePoint,
  switchToOriginalThenCompress,
  goToRunTab,
  toggleSelection,
  showRerunProgress,
  hideRerunProgress,
};
