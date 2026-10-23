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
    if (
      leaf === baseName.toLowerCase()
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
      error: t('video_select.ambiguous', { name: stripped || baseName }),
    };
  }
  return { index: -1, error: t('video_select.not_found') };
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
