/** Label for #btn-select-videos (run-page multi-select toggle). */
import { t } from './i18n.js';

/** @param {boolean} selectionMode */
export function selectVideosButtonLabel(selectionMode) {
  return selectionMode ? t('select.deselect') : t('select.select');
}

/** Full button HTML (icon + label) matching historical toggleSelection markup. */
export function selectVideosButtonHtml(selectionMode) {
  if (selectionMode) {
    return `<span class="icon">✕</span> ${t('select.deselect')}`;
  }
  return `<span class="icon"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg></span> ${t('select.select')}`;
}
