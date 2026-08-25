/**
 * Preload the next Plan preview segment into a hidden <video> so switching
 * sources during composite playback is near-instant (no black flash).
 *
 * Strategy: maintain a single hidden video element. When the active preview
 * segment changes (auto-advance or manual click), resolve the next playable
 * segment and set its src + seek position. The browser buffers data around
 * that position via Range requests; when the main player switches, it
 * re-requests from HTTP cache with minimal latency.
 */

import { videoUrlFor } from './video-url.js';

let _preloadVideo = null;
let _preloadKey = null;
let _metaListener = null;
let _debounceTimer = null;

function getPreloadVideo() {
  if (!_preloadVideo) {
    _preloadVideo = document.createElement('video');
    _preloadVideo.muted = true;
    _preloadVideo.preload = 'auto';
    _preloadVideo.style.display = 'none';
    document.body.appendChild(_preloadVideo);
  }
  return _preloadVideo;
}

function removeMetaListener() {
  if (_metaListener && _preloadVideo) {
    _preloadVideo.removeEventListener('loadedmetadata', _metaListener);
  }
  _metaListener = null;
}

/**
 * Unique key for a preloaded source to avoid stale dedup across
 * source-type or project switches.
 */
function makeKey(v, source, projectName) {
  return [v?.abs_path || v?.file, source, projectName].join('\x00');
}

/**
 * Preload the next playable segment's video at its target seek offset.
 * @param {boolean} previewActive
 * @param {{ segments?: Array<{ videoIndex: string, planStart: number }> }} timeline
 * @param {number} currentSegIndex
 * @param {Function} nextPlayableFn - (timeline, fromIndex) => nextIndex|null
 * @param {string} currentSource - 'compressed' | 'original'
 * @param {Array<{ file: string, index?: string, abs_path?: string, offset_sec?: number }>} videos
 * @param {string|null} projectName
 */
function _doPreload(timeline, currentSegIndex, nextPlayableFn, currentSource, videos, projectName) {
  if (!timeline || !Array.isArray(timeline.segments) || !nextPlayableFn || !Array.isArray(videos)) return;

  const nextIdx = nextPlayableFn(timeline, currentSegIndex);
  if (nextIdx == null) return;
  const seg = timeline.segments[nextIdx];
  if (!seg) return;

  const v = videos.find((x) => String(x.index) === String(seg.videoIndex));
  if (!v) return;

  const key = makeKey(v, currentSource, projectName);
  if (_preloadKey === key) return;
  _preloadKey = key;

  const pv = getPreloadVideo();

  // Clean up any stale listener before switching sources.
  removeMetaListener();

  pv.src = videoUrlFor(v, currentSource, projectName);

  // Seek to segment start (plus original-video offset if applicable).
  // Setting currentTime triggers a Range request around that byte offset,
  // so the browser buffers data near the actual playback start point.
  _metaListener = () => {
    removeMetaListener();
    const offsetSec = currentSource === 'original' ? (v.offset_sec || 0) : 0;
    const target = Math.max(0, (seg.planStart || 0) + offsetSec);
    try {
      if (Number.isFinite(pv.duration) && pv.duration > 0) {
        pv.currentTime = Math.min(target, pv.duration - 0.1);
      }
    } catch { /* ignore seek errors */ }
  };
  pv.addEventListener('loadedmetadata', _metaListener);
}

/**
 * Debounced entry point: during progress-bar scrubbing seekToGlobal fires
 * rapidly; only the last call within the window actually preloads.
 */
export function preloadNextSegment(previewActive, timeline, currentSegIndex, nextPlayableFn, currentSource, videos, projectName) {
  if (!previewActive) return;
  if (_debounceTimer) clearTimeout(_debounceTimer);
  _debounceTimer = setTimeout(() => {
    _debounceTimer = null;
    _doPreload(timeline, currentSegIndex, nextPlayableFn, currentSource, videos, projectName);
  }, 200);
}

/** Clear preload state and release resources. */
export function clearPreload() {
  if (_debounceTimer) { clearTimeout(_debounceTimer); _debounceTimer = null; }
  removeMetaListener();
  _preloadKey = null;
  if (_preloadVideo) {
    try { _preloadVideo.pause(); } catch { /* ignore */ }
    _preloadVideo.removeAttribute('src');
    try { _preloadVideo.load(); } catch { /* ignore */ }
  }
}
