import { state } from './state.js';
import { api } from './api.js';
import { $ } from './utils.js';
import { buildTimeline } from './plan-timeline.js';
import { composePlanPeaks } from './plan-waveform.js';
import { subscribeTaskEvents, fetchTask } from './task-center.js';

/** @type {number} */
let _pollToken = 0;
/** @type {number[]} */
let _lastPeaks = [];
/** @type {ReturnType<typeof setTimeout> | null} */
let _pollTimer = null;
let _waveformTaskUnsubscribe = null;
const _waveformTaskWaiters = new Map();

function _watchWaveformTask(taskId, callback) {
  if (!taskId || typeof callback !== 'function') return;
  _waveformTaskWaiters.set(taskId, callback);
  if (_waveformTaskUnsubscribe) return;
  _waveformTaskUnsubscribe = subscribeTaskEvents(payload => {
    const task = payload?.task;
    if (!task || task.kind !== 'waveform' || !_waveformTaskWaiters.has(task.id)) return;
    if (!['succeeded', 'failed', 'cancelled', 'interrupted'].includes(task.status)) return;
    const waiter = _waveformTaskWaiters.get(task.id);
    _waveformTaskWaiters.delete(task.id);
    try { waiter(task); } catch { /* stale waveform view */ }
    if (!_waveformTaskWaiters.size && _waveformTaskUnsubscribe) {
      _waveformTaskUnsubscribe();
      _waveformTaskUnsubscribe = null;
    }
  });
  fetchTask(taskId).then(task => {
    if (!task || !_waveformTaskWaiters.has(taskId)) return;
    if (!['succeeded', 'failed', 'cancelled', 'interrupted'].includes(task.status)) return;
    const waiter = _waveformTaskWaiters.get(taskId);
    _waveformTaskWaiters.delete(taskId);
    try { waiter(task); } catch { /* stale waveform view */ }
    if (!_waveformTaskWaiters.size && _waveformTaskUnsubscribe) {
      _waveformTaskUnsubscribe();
      _waveformTaskUnsubscribe = null;
    }
  }).catch(() => {});
}

/** @type {'source' | 'plan'} */
let _mode = 'source';
/** @type {number} */
let _planTotal = 0;
/**
 * @type {null | {
 *   isPlan: () => boolean,
 *   seekGlobal: (sec: number) => void,
 *   getGlobalRatio: () => number,
 *   getGlobalSec: () => number,
 * }}
 */
let _planBridge = null;
/** @type {Map<string, { peaks: number[], duration_sec: number } | 'pending' | null>} */
let _planCache = new Map();
/** @type {number} */
let _planLoadToken = 0;

export function timeFromClientX(clientX, barRect, duration) {
  const dur = Number(duration);
  if (!barRect || !(dur > 0) || !Number.isFinite(dur)) return 0;
  const width = barRect.width || 0;
  if (width <= 0) return 0;
  const x = Math.max(0, Math.min(width, clientX - barRect.left));
  return (x / width) * dur;
}

export function playheadRatio(currentTime, duration) {
  const dur = Number(duration);
  if (!(dur > 0) || !Number.isFinite(dur)) return 0;
  const t = Number(currentTime) || 0;
  return Math.max(0, Math.min(1, t / dur));
}

/**
 * Build query fields for GET /api/waveform (project params added by api()).
 * @param {object|null|undefined} v video list entry
 * @param {string} source 'compressed' | 'original'
 */
export function buildWaveformQuery(v, source) {
  if (!v || !v.file) return null;
  const isSegment = Boolean(v.segment_label);
  /** @type {Record<string, string>} */
  const params = { source: source || 'compressed', file: String(v.file) };

  if (isSegment) {
    params.is_segment = '1';
    // Prefer compressed play-file so peaks duration matches the player.
    if (source === 'compressed') {
      params.source = 'compressed';
      params.file = String(v.file);
    } else if (v.match?.file) {
      params.source = 'compressed';
      params.file = String(v.match.file);
    } else if (v.abs_path) {
      params.abspath = String(v.abs_path);
    }
    return params;
  }

  const orig = v.abs_path || v.match?.abs_path;
  const origMissing = Boolean(v.missing || v.match?.missing);
  if (orig && !origMissing) {
    params.abspath = String(orig);
  }
  // When UI is on compressed full file, still pass compressed basename as file fallback.
  if (source === 'compressed' && v.file) {
    params.file = String(v.file);
    params.source = 'compressed';
  }
  return params;
}

export function drawWaveform(canvas, peaks, opts = {}) {
  if (!canvas) return;
  const peaksArr = Array.isArray(peaks) ? peaks : [];
  const dpr = opts.dpr || (typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1);
  const cssW = opts.width || canvas.clientWidth || 300;
  const cssH = opts.height || canvas.clientHeight || 32;
  const w = Math.max(1, Math.floor(cssW * dpr));
  const h = Math.max(1, Math.floor(cssH * dpr));
  if (canvas.width !== w) canvas.width = w;
  if (canvas.height !== h) canvas.height = h;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.clearRect(0, 0, w, h);
  if (!peaksArr.length) return;

  const mid = h / 2;
  const n = peaksArr.length;
  const barW = Math.max(1, w / n);
  const accent = opts.color || 'rgba(99, 160, 255, 0.75)';
  ctx.fillStyle = accent;
  for (let i = 0; i < n; i++) {
    const amp = Math.max(0, Math.min(1, Number(peaksArr[i]) || 0));
    const bh = Math.max(1, amp * (h * 0.9));
    const x = (i / n) * w;
    ctx.fillRect(x, mid - bh / 2, Math.max(1, barW * 0.85), bh);
  }
}

function _barEls() {
  return {
    bar: $('waveform-bar'),
    canvas: $('waveform-canvas'),
    playhead: $('waveform-playhead'),
    status: $('waveform-status'),
  };
}

export function setWaveformPlanBridge(bridge) {
  _planBridge = bridge || null;
}

export function isPlanWaveformMode() {
  return _mode === 'plan';
}

export function getPlanWaveformTotal() {
  return _planTotal;
}

export function resetWaveform() {
  _pollToken += 1;
  _planLoadToken += 1;
  if (_pollTimer) {
    clearTimeout(_pollTimer);
    _pollTimer = null;
  }
  _lastPeaks = [];
  _mode = 'source';
  _planTotal = 0;
  const { bar, canvas, playhead, status } = _barEls();
  if (bar) {
    bar.hidden = true;
    bar.classList.remove('has-peaks');
  }
  if (status) status.textContent = '';
  if (playhead) playhead.hidden = true;
  if (canvas) {
    const ctx = canvas.getContext('2d');
    if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
  }
}

export function setWaveformStatus(text) {
  const { bar, canvas, playhead, status } = _barEls();
  if (bar) {
    bar.hidden = false;
    bar.classList.remove('has-peaks');
  }
  // Drop previous video's peaks so scrub/playhead don't use stale amplitude.
  _lastPeaks = [];
  if (playhead) playhead.hidden = true;
  if (canvas) {
    const ctx = canvas.getContext('2d');
    if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
  }
  if (status) status.textContent = text || '';
}

export function setWaveformPeaks(peaksPayload) {
  const peaks = peaksPayload?.peaks || peaksPayload;
  _lastPeaks = Array.isArray(peaks) ? peaks : [];
  const { bar, canvas, playhead, status } = _barEls();
  if (!bar || !canvas) return;
  bar.hidden = false;
  if (status && !peaksPayload?.keepStatus) status.textContent = '';
  if (_lastPeaks.length) bar.classList.add('has-peaks');
  else bar.classList.remove('has-peaks');
  drawWaveform(canvas, _lastPeaks);
  if (playhead) playhead.hidden = !_lastPeaks.length;
}

export function updateWaveformPlayhead(player) {
  const { bar, playhead } = _barEls();
  if (!bar || !playhead || bar.hidden || !_lastPeaks.length) return;
  let ratio = 0;
  if (_mode === 'plan' && _planBridge?.isPlan?.()) {
    ratio = _planBridge.getGlobalRatio() || 0;
  } else {
    ratio = playheadRatio(player?.currentTime, player?.duration);
  }
  playhead.hidden = false;
  playhead.style.left = `${Math.max(0, Math.min(1, ratio)) * 100}%`;
}

function _seekWaveformRatio(player, ratio) {
  const r = Math.max(0, Math.min(1, Number(ratio) || 0));
  if (_mode === 'plan' && _planBridge?.isPlan?.() && _planTotal > 0) {
    _planBridge.seekGlobal(r * _planTotal);
    updateWaveformPlayhead(player);
    return r;
  }
  if (!player || !(player.duration > 0)) return r;
  try {
    player.currentTime = r * player.duration;
  } catch { /* ignore */ }
  updateWaveformPlayhead(player);
  return r;
}

function _currentWaveformRatio(player) {
  if (_mode === 'plan' && _planBridge?.isPlan?.() && _planTotal > 0) {
    return Math.max(0, Math.min(1, _planBridge.getGlobalRatio?.() || 0));
  }
  const dur = player?.duration;
  if (!(dur > 0)) return 0;
  return Math.max(0, Math.min(1, (player.currentTime || 0) / dur));
}

function _syncWaveformAria(bar, ratio) {
  if (!bar) return;
  const pct = Math.round(Math.max(0, Math.min(1, ratio)) * 100);
  bar.setAttribute('aria-valuenow', String(pct));
}

export function bindWaveformScrub(player) {
  const { bar } = _barEls();
  if (!bar || bar.dataset.bound === '1') return;
  bar.dataset.bound = '1';
  if (!bar.getAttribute('role')) bar.setAttribute('role', 'slider');
  if (!bar.hasAttribute('tabindex')) bar.setAttribute('tabindex', '0');
  if (!bar.getAttribute('aria-label')) bar.setAttribute('aria-label', '波形进度');
  bar.setAttribute('aria-valuemin', '0');
  bar.setAttribute('aria-valuemax', '100');
  _syncWaveformAria(bar, _currentWaveformRatio(player));

  let dragging = false;

  const seekFromEvent = (e) => {
    const rect = bar.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const width = rect.width || 1;
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / width));
    _syncWaveformAria(bar, _seekWaveformRatio(player, ratio));
  };

  bar.addEventListener('pointerdown', (e) => {
    dragging = true;
    bar.setPointerCapture?.(e.pointerId);
    seekFromEvent(e);
    e.preventDefault();
  });
  bar.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    seekFromEvent(e);
  });
  const endDrag = (e) => {
    if (!dragging) return;
    dragging = false;
    try { bar.releasePointerCapture?.(e.pointerId); } catch { /* ignore */ }
  };
  bar.addEventListener('pointerup', endDrag);
  bar.addEventListener('pointercancel', endDrag);

  bar.addEventListener('keydown', (e) => {
    const step = e.shiftKey ? 0.1 : 0.02;
    let next = _currentWaveformRatio(player);
    if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') next -= step;
    else if (e.key === 'ArrowRight' || e.key === 'ArrowUp') next += step;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = 1;
    else return;
    e.preventDefault();
    _syncWaveformAria(bar, _seekWaveformRatio(player, next));
  });

  if (typeof ResizeObserver !== 'undefined') {
    const ro = new ResizeObserver(() => {
      if (_lastPeaks.length) {
        const canvas = $('waveform-canvas');
        drawWaveform(canvas, _lastPeaks);
      }
    });
    ro.observe(bar);
  }
}

async function _fetchWaveform(params, token) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v != null && v !== '') qs.set(k, v);
  }
  const body = await api('GET', `/api/waveform?${qs.toString()}`);
  if (token !== _pollToken && token !== _planLoadToken) return null;
  return body;
}

function _applyPlanCompose(statusText) {
  const seq = state.plan?.sequence || [];
  const tl = buildTimeline(seq);
  /** @type {Record<string, any>} */
  const by = {};
  for (const [k, v] of _planCache.entries()) by[k] = v;
  const composed = composePlanPeaks(tl, by);
  _planTotal = composed.total;
  setWaveformPeaks({
    peaks: composed.peaks,
    keepStatus: Boolean(statusText),
  });
  const { status } = _barEls();
  if (status && statusText) status.textContent = statusText;
  else if (status && !statusText) status.textContent = '';
  const player = $('player');
  if (player) updateWaveformPlayhead(player);
  return composed;
}

export function recomposePlanWaveformFromCache() {
  if (_mode !== 'plan') return;
  // Only 'pending' means generation in flight; null is a failed/missing fetch.
  let pending = false;
  for (const v of _planCache.values()) {
    if (v === 'pending') {
      pending = true;
      break;
    }
  }
  const statusText = pending ? '部分波形生成中…' : '';
  _applyPlanCompose(statusText);
}

/**
 * Load and stitch peaks for all unique video indexes in the current plan.
 */
export async function loadPlanWaveform() {
  const seq = state.plan?.sequence;
  if (!Array.isArray(seq) || !seq.length) {
    resetWaveform();
    return;
  }
  if (state.deps && state.deps.ok === false) {
    _mode = 'plan';
    setWaveformStatus(state.deps.detail || '需要 ffmpeg');
    return;
  }

  const token = ++_planLoadToken;
  _pollToken = token; // cancel any source-mode poll
  if (_pollTimer) {
    clearTimeout(_pollTimer);
    _pollTimer = null;
  }
  _mode = 'plan';

  const tl = buildTimeline(seq);
  _planTotal = tl.total;
  const indexes = [...new Set(
    tl.segments.map((s) => s.videoIndex).filter(Boolean),
  )];

  if (!indexes.length || tl.total <= 0) {
    setWaveformStatus('无可预览波形');
    return;
  }

  setWaveformStatus('波形生成中…');

  /** @type {string[]} */
  let pendingKeys = [];
  let managedPending = 0;

  await Promise.all(indexes.map(async (idx) => {
    if (token !== _planLoadToken) return;
    const v = (state.videos || []).find((x) => String(x.index) === String(idx));
    if (!v) {
      _planCache.set(idx, null);
      return;
    }
    const params = buildWaveformQuery(v, state.source || 'compressed');
    if (!params) {
      _planCache.set(idx, null);
      return;
    }
    try {
      const body = await _fetchWaveform(params, token);
      if (token !== _planLoadToken || !body) return;
      if (body.status === 'pending') {
        _planCache.set(idx, 'pending');
        pendingKeys.push(idx);
        if (body.task_id) {
          managedPending += 1;
          _watchWaveformTask(body.task_id, () => {
            if (token === _planLoadToken) loadPlanWaveform();
          });
        }
        return;
      }
      if (body.no_audio) {
        // Compressed file has no audio stream — nothing to draw for this index.
        _planCache.set(idx, null);
        return;
      }
      if (body.status === 'error' || (!Array.isArray(body.peaks) && body.status !== 'ready')) {
        _planCache.set(idx, null);
        return;
      }
      const peaks = Array.isArray(body.peaks) ? body.peaks : [];
      _planCache.set(idx, {
        peaks,
        duration_sec: Number(body.duration_sec) || 0,
      });
    } catch {
      if (token !== _planLoadToken) return;
      _planCache.set(idx, null);
    }
  }));

  if (token !== _planLoadToken) return;

  // Drop cache keys not in this plan
  for (const k of [..._planCache.keys()]) {
    if (!indexes.includes(k)) _planCache.delete(k);
  }

  pendingKeys = indexes.filter((i) => _planCache.get(i) === 'pending');
  const readyCount = indexes.filter((i) => {
    const e = _planCache.get(i);
    return e && e !== 'pending' && Array.isArray(e.peaks);
  }).length;
  const statusText = pendingKeys.length
    ? (readyCount ? '部分波形生成中…' : '波形生成中…')
    : (readyCount ? '' : '无可用音频源');

  _applyPlanCompose(statusText);

  if (pendingKeys.length && token === _planLoadToken) {
    const pollPending = async (attempt) => {
      if (token !== _planLoadToken) return;
      let still = [];
      await Promise.all(pendingKeys.map(async (idx) => {
        const v = (state.videos || []).find((x) => String(x.index) === String(idx));
        if (!v) {
          _planCache.set(idx, null);
          return;
        }
        const params = buildWaveformQuery(v, state.source || 'compressed');
        if (!params) {
          _planCache.set(idx, null);
          return;
        }
        try {
          const body = await _fetchWaveform(params, token);
          if (token !== _planLoadToken || !body) return;
          if (body.status === 'pending') {
            still.push(idx);
            return;
          }
          if (body.no_audio) {
            _planCache.set(idx, null);
            return;
          }
          if (Array.isArray(body.peaks) || body.status === 'ready') {
            _planCache.set(idx, {
              peaks: Array.isArray(body.peaks) ? body.peaks : [],
              duration_sec: Number(body.duration_sec) || 0,
            });
            return;
          }
          _planCache.set(idx, null);
        } catch {
          _planCache.set(idx, null);
        }
      }));
      if (token !== _planLoadToken) return;
      pendingKeys = still;
      const st = pendingKeys.length ? '部分波形生成中…' : '';
      _applyPlanCompose(st);
      if (pendingKeys.length && attempt < 120) {
        _pollTimer = setTimeout(() => pollPending(attempt + 1), 2500);
      }
    };
    if (managedPending < pendingKeys.length) {
      _pollTimer = setTimeout(() => pollPending(0), 2500);
    }
  }
}

export async function loadWaveformForCurrentVideo() {
  _mode = 'source';
  _planTotal = 0;
  const token = ++_pollToken;
  _planLoadToken = token; // cancel plan loads
  if (_pollTimer) {
    clearTimeout(_pollTimer);
    _pollTimer = null;
  }

  const file = state.currentVideo;
  if (!file) {
    resetWaveform();
    return;
  }
  if (state.deps && state.deps.ok === false) {
    setWaveformStatus(state.deps.detail || '需要 ffmpeg');
    return;
  }
  const v = (state.videos || []).find((x) => x.file === file);
  const params = buildWaveformQuery(v || { file }, state.source || 'compressed');
  if (!params) {
    resetWaveform();
    return;
  }

  setWaveformStatus('波形生成中…');

  const poll = async (attempt) => {
    if (token !== _pollToken) return;
    try {
      const body = await _fetchWaveform(params, token);
      if (token !== _pollToken || !body) return;
      if (body.status === 'pending') {
        setWaveformStatus('波形生成中…');
        if (body.task_id) {
          _watchWaveformTask(body.task_id, () => {
            if (token === _pollToken) loadWaveformForCurrentVideo();
          });
          return;
        }
        if (attempt < 120) {
          _pollTimer = setTimeout(() => poll(attempt + 1), 2500);
        } else {
          setWaveformStatus('波形生成超时');
        }
        return;
      }
      if (body.status === 'error') {
        setWaveformStatus(body.error ? `波形失败: ${body.error}` : '波形生成失败');
        return;
      }
      if (body.no_audio) {
        setWaveformStatus('无可用音频源');
        return;
      }
      if (Array.isArray(body.peaks) || body.status === 'ready') {
        setWaveformPeaks(body);
        const player = $('player');
        if (player) updateWaveformPlayhead(player);
        return;
      }
      setWaveformStatus(body.error || '无波形');
    } catch (err) {
      if (token !== _pollToken) return;
      const msg = err?.message || '波形加载失败';
      const status = err?.status;
      if (status === 404 || String(msg).includes('404') || String(msg).includes('no media')) {
        setWaveformStatus('无可用音频源');
      } else if (status === 503) {
        const detail = err?.body?.error;
        setWaveformStatus(detail ? `波形失败: ${detail}` : '波形生成失败');
      } else {
        setWaveformStatus('波形加载失败');
      }
    }
  };

  await poll(0);
}
