/**
 * Pure helpers for video list step badges and ⋮ menu items.
 * DOM rendering stays in sidebar-data.js.
 */
import { t } from './i18n.js';

/** Whether the compress pipeline step is done for this list entry. */
export function isCompressStepDone(video, source) {
  if (!video) return false;
  if (source === 'compressed') {
    return !video.missing;
  }
  // original view: done if counterpart compressed exists and is online
  const m = video.match;
  return !!(m && m.file && !m.missing);
}

export function buildVideoStepBadges(video, source) {
  return [
    { label: t('menu.step.compress'), done: isCompressStepDone(video, source) },
    { label: t('menu.step.analyze'), done: !!video?.text_json },
    { label: t('menu.step.voiceover'), done: !!video?.script_json },
    { label: t('menu.step.transcribe'), done: !!video?.transcript_file },
  ];
}

const FFMPEG_MENU_ACTIONS = new Set(['compress', 'transcribe', 'label']);

function addRevealAction(items, video) {
  const path = video?.abs_path || video?.match?.abs_path;
  if (video?.missing || !path) return items;
  const removeIndex = items.findIndex(item => item.action === 'remove');
  if (removeIndex < 0) return items;
  const before = items.slice(0, removeIndex);
  if (!before.at(-1)?.divider) before.push({ divider: true });
  return [
    ...before,
    { action: 'reveal', label: t('menu.reveal'), title: t('menu.reveal_title') },
    ...items.slice(removeIndex),
  ];
}

/** Force-disable media actions that hard-require ffmpeg when deps.ok is false. */
export function applyFfmpegMenuDeps(items, deps) {
  if (!deps || deps.ok !== false || !Array.isArray(items)) return items;
  const reason = deps.detail || t('menu.need_ffmpeg');
  return items.map((item) => {
    if (item.action && FFMPEG_MENU_ACTIONS.has(item.action)) {
      return { ...item, disabled: true, title: reason };
    }
    return item;
  });
}

/**
 * @param {object|null|undefined} video
 * @param {string} source
 * @param {{ ok?: boolean, detail?: string }|null} [deps]
 * @returns {Array<{action?: string, label?: string, disabled?: boolean, title?: string, danger?: boolean, divider?: boolean}>}
 */
export function buildVideoMenuItems(video, source, deps = null) {
  const missing = !!video?.missing;
  let items;

  if (source === 'original') {
    if (missing) {
      items = [
        { action: 'compress', label: t('menu.compress'), disabled: true, title: t('menu.offline_title') },
        { action: 'transcribe', label: t('menu.transcribe'), disabled: true, title: t('menu.offline_title') },
        { divider: true },
        {
          action: 'relink',
          label: t('menu.relink'),
          disabled: false,
          title: t('menu.relink_title'),
        },
        {
          action: 'remove',
          label: t('menu.remove'),
          disabled: false,
          danger: true,
          title: t('menu.remove_title'),
        },
      ];
    } else {
      items = [
        {
          action: 'compress',
          label: t('menu.compress'),
          disabled: false,
          title: t('menu.compress_title'),
        },
        {
          action: 'transcribe',
          label: t('menu.transcribe'),
          disabled: false,
          title: t('menu.transcribe_title'),
        },
        {
          action: 'analyze',
          label: t('menu.analyze'),
          disabled: true,
          title: t('menu.analyze_disabled_title'),
        },
        {
          action: 'voiceover',
          label: t('menu.voiceover'),
          disabled: true,
          title: t('menu.voiceover_disabled_title'),
        },
        {
          action: 'all',
          label: t('menu.all'),
          disabled: true,
          title: t('menu.all_disabled_title'),
        },
        { divider: true },
        {
          action: 'remove',
          label: t('menu.remove'),
          disabled: false,
          danger: true,
          title: t('menu.remove_title'),
        },
      ];
    }
    return applyFfmpegMenuDeps(addRevealAction(items, video), deps);
  }

  // compressed view
  if (missing) {
    items = [
      {
        action: 'compress',
        label: t('menu.compress'),
        disabled: true,
        title: t('menu.offline_title'),
      },
      {
        action: 'analyze',
        label: t('menu.analyze'),
        disabled: true,
        title: t('menu.offline_title'),
      },
      {
        action: 'voiceover',
        label: t('menu.voiceover'),
        disabled: true,
        title: t('menu.offline_title'),
      },
      {
        action: 'transcribe',
        label: t('menu.transcribe'),
        disabled: true,
        title: t('menu.offline_title'),
      },
      {
        action: 'all',
        label: t('menu.all'),
        disabled: true,
        title: t('menu.offline_title'),
      },
      { divider: true },
      {
        action: 'remove',
        label: t('menu.remove'),
        disabled: false,
        danger: true,
        title: t('menu.remove_original_title'),
      },
    ];
    return applyFfmpegMenuDeps(items, deps);
  }

  items = [
    {
      action: 'compress',
      label: t('menu.compress'),
      disabled: true,
      title: t('menu.compress_compressed_title'),
    },
    {
      action: 'analyze',
      label: t('menu.analyze'),
      disabled: false,
      title: t('menu.analyze_title'),
    },
    {
      action: 'voiceover',
      label: t('menu.voiceover'),
      disabled: false,
      title: t('menu.voiceover_title'),
    },
    {
      action: 'transcribe',
      label: t('menu.transcribe'),
      disabled: false,
      title: t('menu.transcribe_compressed_title'),
    },
    {
      action: 'all',
      label: t('menu.all'),
      disabled: false,
      title: t('menu.all_title'),
    },
    { divider: true },
    {
      action: 'remove',
      label: t('menu.remove'),
      disabled: false,
      danger: true,
      title: t('menu.remove_original_title'),
    },
  ];
  return applyFfmpegMenuDeps(addRevealAction(items, video), deps);
}

/** Render menu item descriptors to HTML for portal clone. */
export function videoMenuItemsToHtml(items) {
  return items
    .map((it) => {
      if (it.divider) return '<div class="menu-divider"></div>';
      const dis = it.disabled ? 'disabled style="opacity:0.4"' : '';
      const danger = it.danger ? ' menu-item-danger' : '';
      const title = it.title ? ` title="${escapeAttr(it.title)}"` : '';
      return `<button class="menu-item${danger}" data-action="${escapeAttr(it.action || '')}" ${dis}${title}>${escapeText(it.label || '')}</button>`;
    })
    .join('\n');
}

function escapeAttr(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;');
}

function escapeText(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}
