/**
 * Pure helpers for videos.json selection mutations (add / remove).
 */

export function normPath(p) {
  return String(p || '').replace(/\\/g, '/').toLowerCase();
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
      error: `找到多个同名文件 (${stripped || baseName})，请指定完整路径`,
    };
  }
  return { index: -1, error: '未在项目视频列表中找到该文件' };
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
