/** Strip sensitive query params while preserving the rest of the URL (P2-P49). */

/**
 * @param {string} search - location.search (with or without leading ?)
 * @param {string[]} keys - param names to drop
 * @returns {string} search string starting with ? or empty string
 */
export function stripQueryParams(search, keys) {
  const raw = String(search || '');
  const qs = raw.startsWith('?') ? raw.slice(1) : raw;
  if (!qs) return '';
  const params = new URLSearchParams(qs);
  for (const key of keys) params.delete(key);
  const next = params.toString();
  return next ? `?${next}` : '';
}
