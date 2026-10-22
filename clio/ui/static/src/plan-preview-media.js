/** Pure cut-clip-first preview media resolution for the plan timeline. */

import { api } from './api.js';
import { videoUrlFor } from './video-url.js';

const TIME_TOLERANCE = 0.05;

export async function loadPlanPreviewMedia(day, apiFn = api) {
  if (!day) return null;
  try {
    return await apiFn('GET', `/api/plan/preview-media?day=${encodeURIComponent(day)}`);
  } catch {
    return null;
  }
}

function isBlocked(blockedCuts, segIndex) {
  if (!blockedCuts) return false;
  if (typeof blockedCuts.has === 'function') return blockedCuts.has(segIndex);
  return Array.isArray(blockedCuts) && blockedCuts.includes(segIndex);
}

function closeEnough(left, right) {
  const a = Number(left);
  const b = Number(right);
  return Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) <= TIME_TOLERANCE;
}

function clipMatchesTimeline(item, seg) {
  if (!item?.file || !seg) return false;
  if (item.source?.video_index != null && String(item.source.video_index) !== String(seg.videoIndex)) return false;
  if (item.start_sec != null && !closeEnough(item.start_sec, seg.planStart)) return false;
  if (item.end_sec != null && !closeEnough(item.end_sec, seg.planEnd)) return false;
  return true;
}

function clampLocal(seg, localSec) {
  const local = Number(localSec);
  if (!Number.isFinite(local)) return 0;
  const duration = Number(seg?.duration);
  if (!Number.isFinite(duration) || duration <= 0) return Math.max(0, local);
  return Math.min(duration, Math.max(0, local));
}

/**
 * Resolve one timeline segment to a rendered cut clip or the source video.
 * Cut mappings are only accepted when their source index and plan range still
 * match the in-memory timeline; stale mappings fall back immediately.
 */
export function resolvePlanMedia(timeline, segIndex, options = {}) {
  const seg = timeline?.segments?.[segIndex];
  if (!seg) return null;
  const previewMedia = options.previewMedia;
  const item = previewMedia?.segments?.[segIndex];
  if (
    clipMatchesTimeline(item, seg)
    && !isBlocked(options.blockedCuts, segIndex)
    && !options.forceSource
  ) {
    const duration = Number(item.duration_sec) > 0 ? Number(item.duration_sec) : seg.duration;
    const video = {
      file: item.file,
      index: seg.videoIndex,
      source: 'cut',
      cut_day: previewMedia.day,
    };
    return {
      mode: 'cut',
      source: 'cut',
      segIndex,
      video,
      projectName: options.projectName || null,
      url: options.buildUrl === false ? '' : videoUrlFor(video, 'cut', options.projectName || null),
      seekSec: clampLocal(seg, options.localSec),
      endSec: duration,
      durationSec: duration,
      offsetSec: 0,
    };
  }

  const video = (options.videos || []).find((candidate) => String(candidate.index) === String(seg.videoIndex));
  if (!video) return null;
  const offsetSec = options.source === 'original' ? (Number(video.offset_sec) || 0) : 0;
  const plannedDuration = Number(seg.duration) > 0 ? Number(seg.duration) : 0;
  const planEnd = Number.isFinite(Number(seg.planEnd)) ? Number(seg.planEnd) : seg.planStart + plannedDuration;
  return {
    mode: 'source',
    source: options.source || 'compressed',
    segIndex,
    video,
    projectName: options.projectName || null,
    url: options.buildUrl === false
      ? ''
      : videoUrlFor(video, options.source || 'compressed', options.projectName || null),
    seekSec: seg.planStart + clampLocal(seg, options.localSec) + offsetSec,
    endSec: planEnd + offsetSec,
    durationSec: plannedDuration,
    offsetSec,
  };
}

/** Convert native media currentTime into a segment-local and plan-local second. */
export function localSecForPlayer(seg, media, currentTime) {
  if (!seg) return 0;
  if (media?.mode === 'cut') return clampLocal(seg, currentTime);
  return clampLocal(seg, Number(currentTime) - (media?.offsetSec || 0) - seg.planStart);
}

export function previewMediaKey(media) {
  if (!media) return '';
  return [
    media.mode,
    media.source || '',
    media.projectName || '',
    media.video?.cut_day || media.video?.abs_path || media.video?.file || '',
    Number(media.seekSec || 0).toFixed(3),
  ].join('\x00');
}
