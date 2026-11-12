/** Video-list display and cover-thumbnail helpers. */

import { state } from './state.js';
import { escapeHtml } from './utils.js';
import { icon } from './api.js';
import { t } from './i18n.js';

/** Cover thumbnail HTML for video list (falls back to video icon). */
export function videoThumbHtml(v) {
  const name = String(v?.cover_file || '').replace(/\\/g, '/').split('/').pop();
  if (!name) {
    return `<div class="video-thumb">${icon('video')}</div>`;
  }
  const params = new URLSearchParams();
  params.set('file', name);
  if (state.currentProjectName) params.set('project', state.currentProjectName);
  if (state.currentProjectDir) params.set('project_dir', state.currentProjectDir);
  const tok = sessionStorage.getItem('api_token');
  if (tok) params.set('token', tok);
  const src = `/api/cover?${params.toString()}`;
  // Fallback icon under img; onerror on the img swaps to icon via .cover-error.
  return `<div class="video-thumb has-cover" title="${t('sidebar.ai_cover')}">
    <img src="${escapeHtml(src)}" alt="" loading="lazy" decoding="async">
    <span class="video-thumb-fallback">${icon('video')}</span>
  </div>`;
}

export function bindCoverThumbFallback(root) {
  const img = root?.querySelector?.('.video-thumb.has-cover img');
  if (!img) return;
  img.addEventListener('error', () => {
    const thumb = img.closest('.video-thumb');
    if (!thumb) return;
    thumb.classList.add('cover-error');
    thumb.classList.remove('has-cover');
  }, { once: true });
}

export function videoDisplayName(v) {
  let display = v.file.replace(/^\d+_/, '');
  if (v.segment_label) {
    display = display.replace(/_(?:seg|part|pt|chunk)\d+(?=\.[^.]+$)/i, '') + ` [seg ${v.segment_label}]`;
  }
  return v.missing ? t('sidebar.offline_suffix', { name: display }) : display;
}
