/**
 * Video metadata card: hover tooltip on sidebar rows + a pinned modal.
 * Probe work is lazy (first hover/click) and cached per row on the client.
 */
import { $, escapeHtml } from './utils.js';
import { api } from './api.js';
import { t } from './i18n.js';

const HOVER_DELAY_MS = 400;
const _cache = new Map();

let _tipEl = null;
let _hoverTimer = null;
let _hideTimer = null;
let _activeLi = null;
let _dismissBound = false;
let _modalToken = 0;

const HIDE_DELAY_MS = 200;

function cacheKey(video, source) {
  return [
    source === 'original' ? 'original' : 'compressed',
    video?.file || '',
    video?.abs_path || '',
    video?.match?.abs_path || '',
  ].join('|');
}

export function clearVideoInfoCache() {
  _cache.clear();
}

export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined || bytes === '') return '—';
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) return '—';
  if (n < 1024) return `${Math.round(n)} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = n / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

export function formatDuration(sec) {
  if (sec === null || sec === undefined || sec === '') return '—';
  const n = Number(sec);
  if (!Number.isFinite(n) || n < 0) return '—';
  const total = Math.round(n);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (v) => String(v).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

export function formatMtime(epochSec) {
  const n = Number(epochSec);
  if (!Number.isFinite(n) || n <= 0) return '—';
  const d = new Date(n * 1000);
  const pad = (v) => String(v).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function formatFps(fps) {
  const n = Number(fps);
  if (!Number.isFinite(n) || n <= 0) return '—';
  return `${Math.round(n * 100) / 100} fps`;
}

export function formatBitrate(bps) {
  const n = Number(bps);
  if (!Number.isFinite(n) || n <= 0) return '—';
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)} Mbps`;
  return `${Math.round(n / 1e3)} kbps`;
}

function settingsText(settings) {
  if (!settings || typeof settings !== 'object') return '';
  const labels = {
    max_width: t('video_info.setting_max_width'),
    fps: t('video_info.setting_fps'),
    target_size_mb: t('video_info.setting_target_size'),
  };
  return Object.entries(settings)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${labels[key] || key} ${value}`)
    .join(' · ');
}

/** Pure: turn an /api/video/info payload into labelled sections. */
export function buildInfoSections(info) {
  const data = info || {};
  const sections = [];

  const basic = [{ label: t('video_info.path'), value: data.abs_path || '—', wrap: true }];
  if (data.exists === false) {
    basic.push({ label: t('video_info.status'), value: t('video_info.missing') });
  }
  basic.push({ label: t('video_info.size'), value: formatBytes(data.size_bytes) });
  basic.push({ label: t('video_info.duration'), value: formatDuration(data.duration_sec) });
  if (data.mtime) {
    basic.push({ label: t('video_info.mtime'), value: formatMtime(data.mtime) });
  }
  sections.push({ title: t('video_info.section_basic'), rows: basic });

  const media = [];
  if (data.width && data.height) {
    media.push({ label: t('video_info.resolution'), value: `${data.width}×${data.height}` });
  }
  if (data.fps) media.push({ label: t('video_info.fps'), value: formatFps(data.fps) });
  if (data.bit_rate_bps) media.push({ label: t('video_info.bitrate'), value: formatBitrate(data.bit_rate_bps) });
  if (data.codec) media.push({ label: t('video_info.codec'), value: String(data.codec) });
  if (data.format_name) media.push({ label: t('video_info.container'), value: String(data.format_name) });
  if (media.length) sections.push({ title: t('video_info.section_media'), rows: media });

  const compression = data.compression;
  if (compression && typeof compression === 'object') {
    const rows = [
      { label: t('video_info.original_size'), value: formatBytes(compression.source_size_bytes) },
      { label: t('video_info.compressed_size'), value: formatBytes(compression.target_size_bytes) },
    ];
    const ratio = Number(compression.ratio);
    if (Number.isFinite(ratio) && ratio >= 0 && ratio <= 1) {
      rows.push({ label: t('video_info.size_ratio'), value: `${(ratio * 100).toFixed(1)}%` });
    }
    const settings = settingsText(compression.settings);
    if (settings) rows.push({ label: t('video_info.settings'), value: settings, wrap: true });
    sections.push({ title: t('video_info.section_compression'), rows });
  }

  return sections;
}

export function infoSectionsToHtml(sections) {
  return sections
    .map(
      (section) => `<div class="video-info-section">
      <div class="video-info-section-title">${escapeHtml(section.title)}</div>
      ${section.rows
        .map(
          (row) => `<div class="video-info-row${row.wrap ? ' wrap' : ''}">
        <span class="video-info-label">${escapeHtml(row.label)}</span>
        <span class="video-info-value">${escapeHtml(row.value)}</span>
      </div>`
        )
        .join('')}
    </div>`
    )
    .join('');
}

export async function fetchVideoInfo(video, source) {
  const params = new URLSearchParams();
  params.set('source', source === 'original' ? 'original' : 'compressed');
  params.set('file', video?.file || '');
  const abs = video?.abs_path || video?.match?.abs_path || '';
  if (abs) params.set('abspath', abs);
  return api('GET', `/api/video/info?${params.toString()}`);
}

async function _loadInfo(video, source) {
  const key = cacheKey(video, source);
  if (_cache.has(key)) return _cache.get(key);
  try {
    const info = await fetchVideoInfo(video, source);
    _cache.set(key, info);
    return info;
  } catch (e) {
    return { ok: false, error: String(e?.message || e) };
  }
}

function _infoHtml(info) {
  if (!info || info.ok === false) {
    const detail = info?.error ? ` (${info.error})` : '';
    return `<div class="video-info-error">${escapeHtml(t('video_info.failed') + detail)}</div>`;
  }
  return infoSectionsToHtml(buildInfoSections(info));
}

function _ensureTip() {
  if (_tipEl && !_tipEl.isConnected) _tipEl = null;
  if (_tipEl) return _tipEl;
  _tipEl = document.createElement('div');
  _tipEl.className = 'video-info-tooltip';
  _tipEl.setAttribute('role', 'tooltip');
  // Keep the card open while the pointer is on it (copy/scroll long paths).
  _tipEl.addEventListener('mouseenter', _cancelHide);
  _tipEl.addEventListener('mouseleave', () => {
    if (_activeLi) _scheduleHide();
  });
  document.body.appendChild(_tipEl);
  return _tipEl;
}

function _positionTip(li, tip) {
  const rect = li.getBoundingClientRect();
  const tipRect = tip.getBoundingClientRect();
  let left = rect.right + 8;
  if (left + tipRect.width > window.innerWidth - 8) {
    left = Math.max(8, rect.left - tipRect.width - 8);
  }
  let top = rect.top;
  if (top + tipRect.height > window.innerHeight - 8) {
    top = Math.max(8, window.innerHeight - tipRect.height - 8);
  }
  tip.style.left = `${Math.round(left)}px`;
  tip.style.top = `${Math.round(top)}px`;
}

function _hideTip() {
  if (_tipEl) _tipEl.classList.remove('open');
}

function _cancelHide() {
  clearTimeout(_hideTimer);
  _hideTimer = null;
}

function _scheduleHide() {
  _cancelHide();
  _hideTimer = setTimeout(() => {
    _activeLi = null;
    _hideTip();
  }, HIDE_DELAY_MS);
}

function _bindDismiss() {
  if (_dismissBound) return;
  _dismissBound = true;
  const hide = () => {
    _cancelHide();
    _activeLi = null;
    _hideTip();
  };
  window.addEventListener('scroll', (event) => {
    // Let the card scroll its own long content instead of closing itself.
    if (_tipEl && event.target instanceof Node && _tipEl.contains(event.target)) return;
    hide();
  }, true);
  window.addEventListener('resize', hide);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') hide();
  });
}

async function _showTip(li, video, source) {
  _cancelHide();
  const tip = _ensureTip();
  tip.innerHTML = `<div class="video-info-loading">${escapeHtml(t('video_info.loading'))}</div>`;
  tip.classList.add('open');
  _positionTip(li, tip);
  const info = await _loadInfo(video, source);
  if (_activeLi !== li) return;
  tip.innerHTML = _infoHtml(info);
  _positionTip(li, tip);
}

/** Wire delayed hover tooltip on one sidebar row. */
export function bindVideoInfoHover(li, video, source) {
  if (!li || !video) return;
  _bindDismiss();
  li.addEventListener('mouseenter', () => {
    _cancelHide();
    clearTimeout(_hoverTimer);
    if (_activeLi === li && _tipEl?.classList.contains('open')) return;
    if (_activeLi && _activeLi !== li) {
      _activeLi = null;
      _hideTip();
    }
    _hoverTimer = setTimeout(() => {
      _activeLi = li;
      _showTip(li, video, source);
    }, HOVER_DELAY_MS);
  });
  li.addEventListener('mouseleave', () => {
    clearTimeout(_hoverTimer);
    if (_activeLi === li) _scheduleHide();
  });
  li.addEventListener('click', () => {
    _cancelHide();
    clearTimeout(_hoverTimer);
    _activeLi = null;
    _hideTip();
  });
}

export async function openVideoInfoModal(video, source) {
  const modal = $('modal-video-info');
  const body = $('video-info-body');
  if (!modal || !body) return;
  const token = ++_modalToken;
  const nameEl = $('video-info-name');
  if (nameEl) nameEl.textContent = String(video?.file || '').replace(/^\d+_/, '');
  body.innerHTML = `<div class="video-info-loading">${escapeHtml(t('video_info.loading'))}</div>`;
  modal.style.display = 'flex';
  const info = await _loadInfo(video, source);
  if (token !== _modalToken || modal.style.display === 'none') return;
  body.innerHTML = _infoHtml(info);
}

export function closeVideoInfoModal() {
  _modalToken += 1;
  const modal = $('modal-video-info');
  if (modal) modal.style.display = 'none';
}

function _init() {
  const modal = $('modal-video-info');
  if (!modal) return;
  const closeBtn = $('video-info-close');
  if (closeBtn) closeBtn.onclick = closeVideoInfoModal;
  modal.querySelector('.modal-backdrop')?.addEventListener('click', closeVideoInfoModal);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modal.style.display !== 'none') closeVideoInfoModal();
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _init);
} else {
  _init();
}
