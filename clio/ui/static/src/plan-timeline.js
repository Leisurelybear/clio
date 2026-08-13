/** Pure plan composite timeline helpers (no DOM). */

import { parseTimecode } from './utils.js';

function parseRange(useTimeline) {
  const parts = String(useTimeline || '').split('-');
  if (parts.length < 2) return { planStart: 0, planEnd: 0, duration: 0 };
  const planStart = parseTimecode(parts[0].trim());
  const planEnd = parseTimecode(parts[1].trim());
  if (!Number.isFinite(planStart) || !Number.isFinite(planEnd)) {
    return { planStart: 0, planEnd: 0, duration: 0 };
  }
  const duration = Math.max(0, planEnd - planStart);
  return { planStart, planEnd, duration };
}

/**
 * @param {Array<{index?: string, use_timeline?: string}>} sequence
 * @returns {{ segments: Array, total: number }}
 */
export function buildTimeline(sequence) {
  const segs = Array.isArray(sequence) ? sequence : [];
  const segments = [];
  let g = 0;
  for (let i = 0; i < segs.length; i++) {
    const { planStart, planEnd, duration } = parseRange(segs[i]?.use_timeline);
    const globalStart = g;
    const globalEnd = g + duration;
    segments.push({
      segIndex: i,
      videoIndex: String(segs[i]?.index ?? ''),
      planStart,
      planEnd,
      duration,
      globalStart,
      globalEnd,
    });
    g = globalEnd;
  }
  return { segments, total: g };
}

/**
 * @param {{ total?: number }} timeline
 * @param {number} globalSec
 * @returns {number}
 */
export function clampGlobal(timeline, globalSec) {
  const total = timeline?.total || 0;
  const t = Number(globalSec);
  if (!Number.isFinite(t) || t < 0) return 0;
  if (total <= 0) return 0;
  if (t > total) return total;
  return t;
}

/**
 * Next segment index with duration > 0 after fromIndex (exclusive).
 * fromIndex = -1 means first playable.
 * @param {{ segments?: Array }} timeline
 * @param {number} fromIndex
 * @returns {number|null}
 */
export function nextPlayableSegIndex(timeline, fromIndex) {
  const segs = timeline?.segments || [];
  const start = (Number(fromIndex) | 0) + 1;
  for (let i = Math.max(0, start); i < segs.length; i++) {
    if (segs[i].duration > 0) return i;
  }
  return null;
}

/** Last playable segment at or before *atOrBefore* (P2-P43 end-edge fallback). */
export function lastPlayableSegIndex(timeline, atOrBefore = Infinity) {
  const segs = timeline?.segments || [];
  const end = Math.min(segs.length - 1, Number.isFinite(atOrBefore) ? atOrBefore : segs.length - 1);
  for (let i = end; i >= 0; i--) {
    if (segs[i].duration > 0) return i;
  }
  return null;
}

/**
 * @param {{ segments?: Array, total?: number }} timeline
 * @param {number} globalSec
 * @returns {{ segIndex: number, localSec: number, planSec: number, videoIndex: string } | null}
 */
export function globalToLocal(timeline, globalSec) {
  const segs = timeline?.segments || [];
  if (!segs.length) return null;
  const t = clampGlobal(timeline, globalSec);

  let idx = segs.length - 1;
  for (let i = 0; i < segs.length; i++) {
    if (t < segs[i].globalEnd) {
      idx = i;
      break;
    }
  }

  if (segs[idx].duration === 0) {
    const n = lastPlayableSegIndex(timeline, idx);
    if (n != null) idx = n;
  }

  const seg = segs[idx];
  const localSec = seg.duration > 0
    ? Math.min(seg.duration, Math.max(0, t - seg.globalStart))
    : 0;
  return {
    segIndex: idx,
    localSec,
    planSec: seg.planStart + localSec,
    videoIndex: seg.videoIndex,
  };
}

/**
 * @param {{ segments?: Array }} timeline
 * @param {number} segIndex
 * @param {number} localSec
 * @returns {number}
 */
export function localToGlobal(timeline, segIndex, localSec) {
  const seg = timeline?.segments?.[segIndex];
  if (!seg) return 0;
  const local = Number(localSec);
  const l = Number.isFinite(local) ? Math.min(seg.duration, Math.max(0, local)) : 0;
  return seg.globalStart + l;
}

/**
 * Width fractions 0..1 per segment (sum ≈ 1).
 * @param {{ segments?: Array, total?: number }} timeline
 * @returns {number[]}
 */
export function segmentWidths(timeline) {
  const segs = timeline?.segments || [];
  const n = segs.length;
  if (!n) return [];
  const total = timeline.total || 0;
  if (total <= 0) return segs.map(() => 1 / n);
  return segs.map((s) => s.duration / total);
}

/**
 * Map plan-domain seconds (source currentTime − offset_sec) onto a segment
 * that uses the same video index. Used after native <video> seek/play so
 * auto-advance end time and composite playhead stay aligned.
 *
 * @param {{ segments?: Array }} timeline
 * @param {string|number} videoIndex
 * @param {number} planSec
 * @returns {{ segIndex: number, localSec: number, planSec: number, videoIndex: string, pastEnd: boolean } | null}
 */
export function locateSegmentByPlanSec(timeline, videoIndex, planSec) {
  const segs = timeline?.segments || [];
  if (!segs.length) return null;
  const idxKey = String(videoIndex ?? '');
  const t = Number(planSec);
  const plan = Number.isFinite(t) ? t : 0;

  const matches = [];
  for (let i = 0; i < segs.length; i++) {
    const s = segs[i];
    if (String(s.videoIndex) !== idxKey) continue;
    if (!(s.duration > 0)) continue;
    matches.push(s);
  }
  if (!matches.length) return null;

  for (const s of matches) {
    if (plan >= s.planStart && plan < s.planEnd) {
      return {
        segIndex: s.segIndex,
        localSec: plan - s.planStart,
        planSec: plan,
        videoIndex: s.videoIndex,
        pastEnd: false,
      };
    }
  }

  // Before first match → clamp to start of earliest planStart among matches
  const byStart = [...matches].sort((a, b) => a.planStart - b.planStart);
  if (plan < byStart[0].planStart) {
    const s = byStart[0];
    return {
      segIndex: s.segIndex,
      localSec: 0,
      planSec: s.planStart,
      videoIndex: s.videoIndex,
      pastEnd: false,
    };
  }

  // At/after a segment end (or past last): pick last match whose planStart ≤ plan
  let best = byStart[0];
  for (const s of byStart) {
    if (s.planStart <= plan) best = s;
  }
  const pastEnd = plan >= best.planEnd;
  return {
    segIndex: best.segIndex,
    localSec: pastEnd ? best.duration : Math.min(best.duration, Math.max(0, plan - best.planStart)),
    planSec: pastEnd ? best.planEnd : plan,
    videoIndex: best.videoIndex,
    pastEnd,
  };
}
