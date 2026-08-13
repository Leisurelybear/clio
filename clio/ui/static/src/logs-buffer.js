/** Session-log client buffer helpers (P2-P38). */

export const LOGS_BUFFER_MAX = 2000;

/**
 * Append entries and trim to a ring-buffer capacity (drop oldest).
 * Mutates `buffer` in place and returns it.
 * @param {unknown[]} buffer
 * @param {unknown[]} entries
 * @param {number} [max=LOGS_BUFFER_MAX]
 * @returns {unknown[]}
 */
export function appendLogEntries(buffer, entries, max = LOGS_BUFFER_MAX) {
  if (!Array.isArray(entries) || entries.length === 0) return buffer;
  for (const entry of entries) {
    buffer.push(entry);
  }
  const overflow = buffer.length - max;
  if (overflow > 0) {
    buffer.splice(0, overflow);
  }
  return buffer;
}
