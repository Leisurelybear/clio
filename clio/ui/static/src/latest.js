/**
 * Latest-wins request slots: abort the previous in-flight call for a key.
 * Used to prevent stale responses from overwriting newer UI state (P2-P37).
 */

const _controllers = new Map();

/** Abort any prior request for *key* and return a fresh AbortController. */
export function beginLatest(key) {
  const prev = _controllers.get(key);
  if (prev) prev.abort();
  const ac = new AbortController();
  _controllers.set(key, ac);
  return ac;
}

export function isLatest(key, ac) {
  return _controllers.get(key) === ac;
}

/** Drop the slot only if *ac* is still the active controller. */
export function endLatest(key, ac) {
  if (_controllers.get(key) === ac) {
    _controllers.delete(key);
  }
}

export function isAbortError(err) {
  return !!err && (err.name === 'AbortError' || err.code === 20);
}
