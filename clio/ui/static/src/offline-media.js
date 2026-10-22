/**
 * Offline media helpers (pure). Used by sidebar summary + batch relink.
 */

export function basenameLower(path) {
  const s = String(path || '');
  const leaf = s.replace(/^.*[\\/]/, '');
  return leaf.toLowerCase();
}

/** Strip optional leading index like "001_" from a filename for matching. */
export function stemKey(pathOrName) {
  const base = basenameLower(pathOrName);
  return base.replace(/^\d+_/, '');
}

export function summarizeOfflineVideos(videos = []) {
  const items = (videos || [])
    .filter(v => v && v.missing)
    .map(v => ({
      file: v.file,
      abs_path: v.abs_path || v.match?.abs_path || null,
    }));
  return { count: items.length, items };
}

/**
 * Match offline project entries to candidate files found under a directory.
 * @param {Array<{file: string, abs_path?: string|null}>} offline
 * @param {Array<{path: string, name?: string}>} candidates
 */
export function matchBatchRelink(offline = [], candidates = []) {
  const byBase = new Map(); // lowercase basename -> candidate[]
  const byStem = new Map(); // stripped stem -> candidate[]
  for (const c of candidates) {
    const path = String(c?.path || '');
    if (!path) continue;
    const base = basenameLower(c.name || path);
    const key = stemKey(base);
    if (!base) continue;
    if (!byBase.has(base)) byBase.set(base, []);
    byBase.get(base).push(c);
    if (key) {
      if (!byStem.has(key)) byStem.set(key, []);
      byStem.get(key).push(c);
    }
  }

  const matched = [];
  const unmatched = [];
  const ambiguous = [];
  const seenAmbiguous = new Set();
  const usedPaths = new Set(); // each candidate path at most once
  const pathKey = (p) => String(p || '').replace(/\\/g, '/').toLowerCase();
  const unused = (items) => items.filter(h => !usedPaths.has(pathKey(h.path)));

  for (const item of offline) {
    const absBase = basenameLower(item.abs_path || '');
    const displayBase = basenameLower(item.file || '');
    let ambiguityKey = stemKey(item.file || item.abs_path || '');
    let hits = [];

    for (const base of new Set([absBase, displayBase])) {
      if (!base) continue;
      hits = unused(byBase.get(base) || []);
      if (hits.length) {
        ambiguityKey = base;
        break;
      }
    }
    if (!hits.length) {
      hits = unused(byStem.get(ambiguityKey) || []);
    }

    if (hits.length === 1) {
      usedPaths.add(pathKey(hits[0].path));
      matched.push({
        file: item.file,
        old_path: item.abs_path || item.file,
        new_path: hits[0].path,
      });
    } else if (hits.length > 1) {
      if (!seenAmbiguous.has(ambiguityKey)) {
        seenAmbiguous.add(ambiguityKey);
        ambiguous.push({ basename: ambiguityKey, candidates: hits.map(h => h.path) });
      }
      unmatched.push(item);
    } else {
      unmatched.push(item);
    }
  }

  return { matched, unmatched, ambiguous };
}
