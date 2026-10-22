import { state } from './state.js';
import { $ } from './utils.js';
import { t } from './i18n.js';
import {
  formatTimelineSec,
  selectionFromUseTimeline,
  useTimelineFromFileSelection,
  clampFileSelection,
  planSecFromPlayer,
} from './plan-edit.js';

let _bound = false;
let _segIndex = -1;
let _offsetSec = 0;
let _duration = 0;
let _startSec = 0;
let _endSec = 0;
let _onApply = null;
let _dragWhich = null; // 'start' | 'end' | null
let _onMove = null;
let _onUp = null;

function videoUrlFor(v) {
  const projParam = state.currentProjectName
    ? `&project=${encodeURIComponent(state.currentProjectName)}` : '';
  const tokenParam = sessionStorage.getItem('api_token');
  const extraParam = tokenParam ? `&token=${encodeURIComponent(tokenParam)}` : '';
  const absParam = v?.abs_path ? `&abspath=${encodeURIComponent(v.abs_path)}` : '';
  const source = state.source || 'compressed';
  return `/api/video?file=${encodeURIComponent(v.file)}&source=${source}${absParam}${projParam}${extraParam}`;
}

function minSpanForDuration(duration) {
  const d = Number(duration);
  if (!Number.isFinite(d) || d <= 0) return 1;
  return d < 1 ? d : 1;
}

function setStartFromDrag(sec) {
  const min = minSpanForDuration(_duration);
  let start = Number(sec);
  if (!Number.isFinite(start)) start = 0;
  start = Math.max(0, Math.min(start, _duration));
  const maxStart = Math.max(0, _endSec - min);
  _startSec = Math.min(start, maxStart);
  // Keep end fixed; only re-clamp if duration edge cases need it.
  const fixed = clampFileSelection({
    startSec: _startSec,
    endSec: _endSec,
    duration: _duration,
    minSpan: min,
  });
  _startSec = fixed.startSec;
  _endSec = fixed.endSec;
}

function setEndFromDrag(sec) {
  const min = minSpanForDuration(_duration);
  let end = Number(sec);
  if (!Number.isFinite(end)) end = _duration;
  end = Math.max(0, Math.min(end, _duration));
  const minEnd = Math.min(_duration, _startSec + min);
  _endSec = Math.max(end, minEnd);
  const fixed = clampFileSelection({
    startSec: _startSec,
    endSec: _endSec,
    duration: _duration,
    minSpan: min,
  });
  _startSec = fixed.startSec;
  _endSec = fixed.endSec;
}

function paintHandles() {
  const fill = $('plan-range-fill');
  const hs = $('plan-range-handle-start');
  const he = $('plan-range-handle-end');
  const d = _duration > 0 ? _duration : 1;
  const leftPct = (_startSec / d) * 100;
  const rightPct = (_endSec / d) * 100;
  if (hs) hs.style.left = `${leftPct}%`;
  if (he) he.style.left = `${rightPct}%`;
  if (fill) {
    fill.style.left = `${leftPct}%`;
    fill.style.width = `${Math.max(0, rightPct - leftPct)}%`;
  }
  const sl = $('plan-range-start-label');
  const el = $('plan-range-end-label');
  const dl = $('plan-range-dur-label');
  const pl = $('plan-range-plan-label');
  if (sl) sl.textContent = formatTimelineSec(_startSec);
  if (el) el.textContent = formatTimelineSec(_endSec);
  if (dl) dl.textContent = `/ ${formatTimelineSec(_duration)}`;
  if (pl) {
    if (_offsetSec > 0) {
      const ps = planSecFromPlayer(_startSec, _offsetSec) ?? 0;
      const pe = planSecFromPlayer(_endSec, _offsetSec) ?? ps;
      pl.textContent = t('plan.range.plan_offset', { start: formatTimelineSec(ps), end: formatTimelineSec(pe) });
    } else {
      pl.textContent = '';
    }
  }
}

function seekToHandle(which) {
  const video = $('plan-range-video');
  if (!video) return;
  const t = which === 'end' ? _endSec : _startSec;
  try {
    video.currentTime = t;
  } catch { /* ignore seek before ready */ }
}

function setError(msg) {
  const el = $('plan-range-error');
  if (!el) return;
  if (!msg) {
    el.style.display = 'none';
    el.textContent = '';
    return;
  }
  el.style.display = 'block';
  el.textContent = msg;
}

function setApplyEnabled(ok) {
  const btn = $('plan-range-apply');
  if (btn) btn.disabled = !ok;
}

function stopDragListeners() {
  if (_onMove) document.removeEventListener('pointermove', _onMove);
  if (_onUp) {
    document.removeEventListener('pointerup', _onUp);
    document.removeEventListener('pointercancel', _onUp);
  }
  // Keep _onMove/_onUp function refs — ensureBound only creates them once.
  _dragWhich = null;
}

function syncPlayButton() {
  const video = $('plan-range-video');
  const btn = $('plan-range-play');
  if (!btn) return;
  const playing = video && !video.paused && !video.ended;
  btn.textContent = playing ? t('plan.range.pause') : t('plan.range.play');
}

function togglePlay() {
  const video = $('plan-range-video');
  if (!video || !video.src) return;
  if (video.paused || video.ended) {
    video.play().catch(() => {});
  } else {
    video.pause();
  }
  // play/pause events also update label; sync immediately for snappy UI
  setTimeout(syncPlayButton, 0);
}

function applySelection() {
  if (typeof _onApply !== 'function') {
    closePlanRangePicker();
    return;
  }
  const use_timeline = useTimelineFromFileSelection(_startSec, _endSec, _offsetSec);
  const segIndex = _segIndex;
  const cb = _onApply;
  closePlanRangePicker();
  cb({ segIndex, use_timeline });
}

function ensureBound() {
  if (_bound) return;
  _bound = true;
  const modal = $('modal-plan-range');
  modal?.querySelector('.modal-backdrop')?.addEventListener('click', closePlanRangePicker);
  $('plan-range-cancel')?.addEventListener('click', closePlanRangePicker);
  $('plan-range-apply')?.addEventListener('click', applySelection);
  $('plan-range-play')?.addEventListener('click', (e) => {
    e.stopPropagation();
    togglePlay();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if ($('modal-plan-range')?.style.display !== 'flex') return;
    closePlanRangePicker();
  });

  const video = $('plan-range-video');
  if (video) {
    video.addEventListener('play', syncPlayButton);
    video.addEventListener('pause', syncPlayButton);
    video.addEventListener('ended', syncPlayButton);
  }

  const track = $('plan-range-track');
  _onMove = (e) => {
    if (!_dragWhich || !track) return;
    const rect = track.getBoundingClientRect();
    if (rect.width <= 0) return;
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const sec = ratio * _duration;
    if (_dragWhich === 'start') setStartFromDrag(sec);
    else setEndFromDrag(sec);
    paintHandles();
    seekToHandle(_dragWhich);
  };
  _onUp = () => {
    if (!_dragWhich) return;
    stopDragListeners();
  };

  ['plan-range-handle-start', 'plan-range-handle-end'].forEach((id) => {
    $(id)?.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const el = e.currentTarget;
      // Restarting a drag: drop previous listeners first.
      stopDragListeners();
      _dragWhich = el?.dataset?.which === 'end' ? 'end' : 'start';
      try { el.setPointerCapture?.(e.pointerId); } catch { /* ignore */ }
      document.addEventListener('pointermove', _onMove);
      document.addEventListener('pointerup', _onUp);
      document.addEventListener('pointercancel', _onUp);
      // Pause while scrubbing handles so playhead stays on the bound.
      try { video?.pause(); } catch { /* ignore */ }
      seekToHandle(_dragWhich);
      syncPlayButton();
    });
  });
}

export function closePlanRangePicker() {
  stopDragListeners();
  const modal = $('modal-plan-range');
  if (modal) modal.style.display = 'none';
  const video = $('plan-range-video');
  if (video) {
    try { video.pause(); } catch { /* ignore */ }
    video.onloadedmetadata = null;
    video.onerror = null;
    video.removeAttribute('src');
    try { video.load(); } catch { /* ignore */ }
  }
  _segIndex = -1;
  _onApply = null;
  _duration = 0;
  _startSec = 0;
  _endSec = 0;
  _offsetSec = 0;
  setError('');
  setApplyEnabled(false);
  syncPlayButton();
  const pl = $('plan-range-plan-label');
  if (pl) pl.textContent = '';
}

/**
 * @param {{
 *   segIndex: number,
 *   video: { file: string, abs_path?: string, offset_sec?: number, index?: string, title?: string },
 *   useTimeline: string,
 *   title?: string,
 *   onApply: (p: { segIndex: number, use_timeline: string }) => void,
 * }} opts
 */
export function openPlanRangePicker(opts) {
  ensureBound();
  const modal = $('modal-plan-range');
  const video = $('plan-range-video');
  if (!modal || !video || !opts?.video?.file) return;

  // Reset any previous open state without wiping the modal shell permanently.
  stopDragListeners();
  try { video.pause(); } catch { /* ignore */ }

  _segIndex = Number(opts.segIndex) | 0;
  _offsetSec = Number(opts.video.offset_sec) || 0;
  if (!Number.isFinite(_offsetSec) || _offsetSec < 0) _offsetSec = 0;
  _onApply = opts.onApply;
  _duration = 0;
  _startSec = 0;
  _endSec = 0;
  setApplyEnabled(false);
  setError('');

  const heading = $('plan-range-title');
  if (heading) {
    const idx = opts.video.index != null ? String(opts.video.index) : '?';
    const name = opts.title || opts.video.title || opts.video.file || '';
    heading.textContent = t('plan.range.heading', { index: _segIndex + 1, vid: idx, name });
  }

  const onMeta = () => {
    _duration = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : 0;
    if (_duration <= 0) {
      setError(t('plan.range.no_duration'));
      setApplyEnabled(false);
      return;
    }
    const sel = selectionFromUseTimeline(opts.useTimeline || '', _duration, _offsetSec);
    _startSec = sel.startSec;
    _endSec = sel.endSec;
    paintHandles();
    seekToHandle('start');
    setApplyEnabled(true);
  };
  const onErr = () => {
    setError(t('plan.range.load_failed'));
    setApplyEnabled(false);
  };

  video.onloadedmetadata = onMeta;
  video.onerror = onErr;
  // Force reload even if same file as previous open.
  video.removeAttribute('src');
  try { video.load(); } catch { /* ignore */ }
  video.src = videoUrlFor(opts.video);
  modal.style.display = 'flex';
  paintHandles();
  syncPlayButton();
}
