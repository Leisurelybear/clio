/**
 * Pure helpers for videos.json selection mutations (add / remove).
 */
import { t } from './i18n.js';

export function normPath(p) {
  const raw = String(p || '').replace(/\\/g, '/').toLowerCase();
  if (!raw) return '';
  const absolute = raw.startsWith('/');
  const parts = [];
  for (const seg of raw.split('/')) {
    if (!seg || seg === '.') continue;
    if (seg === '..') {
      if (parts.length && parts[parts.length - 1] !== '..') parts.pop();
      else if (!absolute) parts.push('..');
      continue;
    }
    parts.push(seg);
  }
  const joined = parts.join('/');
  return absolute ? `/${joined}` : joined;
}

/**
 * Find which index in selected paths to remove.
 * Prefer absolute path equality; basename fallback only when unique.
 * @returns {{ index: number, error?: string }}
 */
export function findSelectedVideoIndex(videos = [], { file, absPath } = {}) {
  const list = Array.isArray(videos) ? videos : [];
  const targetAbs = absPath ? normPath(absPath) : '';
  const display = String(file || '');
  const baseName = display.replace(/^.*[\\/]/, '');
  const stripped = baseName.replace(/^\d+_/, '');

  if (targetAbs) {
    const idx = list.findIndex(p => normPath(p) === targetAbs);
    if (idx !== -1) return { index: idx };
  }

  const basenameHits = [];
  for (let i = 0; i < list.length; i++) {
    const n = normPath(list[i]);
    const leaf = n.split('/').pop() || '';
    const leafStripped = leaf.replace(/^\d+_/, '');
    if (
      leaf === baseName.toLowerCase()
      || leafStripped === baseName.toLowerCase()
      || leaf === stripped.toLowerCase()
      || n.endsWith('/' + baseName.toLowerCase())
      || n.endsWith('/' + stripped.toLowerCase())
      || n === display.toLowerCase()
    ) {
      basenameHits.push(i);
    }
  }
  if (basenameHits.length === 1) return { index: basenameHits[0] };
  if (basenameHits.length > 1) {
    return {
      index: -1,
      reason: 'ambiguous',
      error: t('video_select.ambiguous', { name: stripped || baseName }),
    };
  }
  return { index: -1, reason: 'not_found', error: t('video_select.not_found') };
}

/**
 * Merge newly chosen absolute paths into existing selection.
 * @returns {{ merged: string[], added: number, already: number }}
 */
export function mergeSelectedVideos(existing = [], candidates = []) {
  const set = new Set();
  const merged = [];
  for (const raw of existing || []) {
    const p = String(raw).replace(/\\/g, '/');
    const key = normPath(p);
    if (!key || set.has(key)) continue;
    set.add(key);
    merged.push(p);
  }
  let added = 0;
  let already = 0;
  for (const raw of candidates || []) {
    const p = String(raw).replace(/\\/g, '/');
    const key = normPath(p);
    if (!key) continue;
    if (set.has(key)) {
      already++;
    } else {
      set.add(key);
      merged.push(p);
      added++;
    }
  }
  return { merged, added, already };
}

/**
 * Remove several targets from a stored selection list in one pass.
 * Each target is `{ file, absPath }`; matching reuses findSelectedVideoIndex,
 * so absolute-path equality wins and a unique basename is the fallback.
 * @returns {{ remaining: string[], removed: string[], missing: string[], ambiguous: string[] }}
 */
export function removeSelectedVideos(videos = [], targets = []) {
  const remaining = (Array.isArray(videos) ? videos : []).slice();
  const removed = [];
  const missing = [];
  const ambiguous = [];
  const seen = new Set();
  for (const target of Array.isArray(targets) ? targets : []) {
    if (!target) continue;
    const display = String(target.file ?? target.absPath ?? '');
    const key = normPath(target.absPath || display);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    const found = findSelectedVideoIndex(remaining, target);
    if (found.index === -1) {
      if (found.reason === 'ambiguous') ambiguous.push(found.error);
      else missing.push(display);
      continue;
    }
    removed.push(remaining[found.index]);
    remaining.splice(found.index, 1);
  }
  return { remaining, removed, missing, ambiguous };
}

/**
 * Toggle a filtered subset in a selection list.
 *
 * When every `selectableFiles` entry is already selected, remove only those
 * entries and keep selections made outside the current filter. Otherwise add
 * the whole subset. The result is always deduplicated.
 *
 * @param {string[]} selectedFiles
 * @param {string[]} selectableFiles
 * @returns {string[]}
 */
export function toggleSelectionFor(selectedFiles = [], selectableFiles = []) {
  const list = (Array.isArray(selectedFiles) ? selectedFiles : []).slice();
  const target = (Array.isArray(selectableFiles) ? selectableFiles : []).filter(Boolean);
  if (!target.length) return list;
  const selected = new Set(list);
  if (target.every((file) => selected.has(file))) {
    const drop = new Set(target);
    return list.filter((file) => !drop.has(file));
  }
  const added = target.filter((file) => !selected.has(file));
  return [...list, ...added];
}
