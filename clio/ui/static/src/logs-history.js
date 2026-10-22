/** Pure helpers for browsing hourly disk logs (R-027e). */

import { t } from './i18n.js';

const LOG_FILE_RE = /^(\d{4})-(\d{2})-(\d{2})-(\d{2})\.log$/;

/**
 * Format a safe hourly log basename for display in local wall-clock terms.
 * @param {string} name
 * @returns {string}
 */
export function formatLogHour(name) {
  const raw = String(name ?? '');
  const match = LOG_FILE_RE.exec(raw);
  if (!match) return raw;
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  if (month < 1 || month > 12 || day < 1 || day > 31 || hour > 23) return raw;
  return `${match[1]}-${match[2]}-${match[3]} ${match[4]}:00`;
}

/**
 * @param {unknown} bytes
 * @returns {string}
 */
export function formatLogFileSize(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value < 0) return '';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * @param {{ name?: string, size?: number }} file
 * @returns {string}
 */
export function logFileLabel(file) {
  const hour = formatLogHour(file?.name ?? '');
  const size = formatLogFileSize(file?.size);
  return size ? `${hour} · ${size}` : hour;
}

/**
 * Return API files newest-first, ignoring malformed entries.
 * @param {unknown} files
 * @returns {Array<{name: string, size?: number, mtime?: number}>}
 */
export function sortLogFiles(files) {
  if (!Array.isArray(files)) return [];
  return files
    .filter((file) => file && typeof file === 'object' && typeof file.name === 'string')
    .slice()
    .sort((a, b) => b.name.localeCompare(a.name));
}

/**
 * @param {{ name?: string, logs?: unknown[], truncated?: boolean }} result
 * @returns {string}
 */
export function historyStatusText(result = {}) {
  const count = Array.isArray(result.logs) ? result.logs.length : 0;
  const parts = [`${formatLogHour(result.name ?? '')} · ${t('logs.line_count', { count })}`];
  if (result.truncated) parts.push(t('logs.tail_only'));
  return parts.join(' · ');
}
