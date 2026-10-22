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

import { resolvePlanMedia, previewMediaKey } from './plan-preview-media.js';
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
/**
 * Preload the next playable segment's video at its target seek offset.
 * @param {boolean} previewActive
 * @param {{ segments?: Array<{ videoIndex: string, planStart: number }> }} timeline
 * @param {number} currentSegIndex
 * @param {Function} nextPlayableFn - (timeline, fromIndex) => nextIndex|null
 * @param {string} currentSource - 'compressed' | 'original'
 * @param {Array<{ file: string, index?: string, abs_path?: string, offset_sec?: number }>} videos
 * @param {string|null} projectName
 * @param {object|null} previewMedia
 * @param {Set<number>|Array<number>|null} blockedCuts
 */
function _doPreload(
  timeline,
  currentSegIndex,
  nextPlayableFn,
  currentSource,
  videos,
  projectName,
  previewMedia = null,
  blockedCuts = null,
) {
  if (!timeline || !Array.isArray(timeline.segments) || !nextPlayableFn || !Array.isArray(videos)) return;

  const nextIdx = nextPlayableFn(timeline, currentSegIndex);
  if (nextIdx == null) return;
  const seg = timeline.segments[nextIdx];
  if (!seg) return;

  const media = resolvePlanMedia(timeline, nextIdx, {
    previewMedia,
    videos,
    source: currentSource,
    projectName,
    localSec: 0,
    blockedCuts,
    buildUrl: false,
  });
  if (!media) return;

  const key = previewMediaKey(media);
  if (_preloadKey === key) return;
  _preloadKey = key;

  const pv = getPreloadVideo();

  // Clean up any stale listener before switching sources.
  removeMetaListener();

  const source = media.mode === 'cut' ? 'cut' : currentSource;
  pv.src = videoUrlFor(media.video, source, projectName);

  // Seek to segment start (plus original-video offset if applicable).
  // Setting currentTime triggers a Range request around that byte offset,
  // so the browser buffers data near the actual playback start point.
  _metaListener = () => {
    removeMetaListener();
    const target = Math.max(0, media.seekSec);
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
export function preloadNextSegment(
  previewActive,
  timeline,
  currentSegIndex,
  nextPlayableFn,
  currentSource,
  videos,
  projectName,
  previewMedia = null,
  blockedCuts = null,
) {
  if (!previewActive) return;
  if (_debounceTimer) clearTimeout(_debounceTimer);
  _debounceTimer = setTimeout(() => {
    _debounceTimer = null;
    _doPreload(
      timeline,
      currentSegIndex,
      nextPlayableFn,
      currentSource,
      videos,
      projectName,
      previewMedia,
      blockedCuts,
    );
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
    _preloadVideo.remove();
    _preloadVideo = null;
  }
}
