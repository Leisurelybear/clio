/**
 * Pure helpers for video list step badges and ⋮ menu items.
 * DOM rendering stays in sidebar-data.js.
 */

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
    { label: '压缩', done: isCompressStepDone(video, source) },
    { label: '分析', done: !!video?.text_json },
    { label: '口播', done: !!video?.script_json },
    { label: '转录', done: !!video?.transcript_file },
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
    { action: 'reveal', label: '在文件管理器中显示', title: '打开所在目录并选中此文件' },
    ...items.slice(removeIndex),
  ];
}

/** Force-disable media actions that hard-require ffmpeg when deps.ok is false. */
export function applyFfmpegMenuDeps(items, deps) {
  if (!deps || deps.ok !== false || !Array.isArray(items)) return items;
  const reason = deps.detail || '需要 ffmpeg/ffprobe';
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
        { action: 'compress', label: '压缩视频', disabled: true, title: '文件离线' },
        { action: 'transcribe', label: 'Whisper 转录', disabled: true, title: '文件离线' },
        { divider: true },
        {
          action: 'relink',
          label: '重新关联路径...',
          disabled: false,
          title: '文件移动或重命名后，重新关联新路径',
        },
        {
          action: 'remove',
          label: '从项目移除',
          disabled: false,
          danger: true,
          title: '从项目中移除该视频',
        },
      ];
    } else {
      items = [
        {
          action: 'compress',
          label: '压缩视频',
          disabled: false,
          title: '用 ffmpeg 将原视频压缩为 640p',
        },
        {
          action: 'transcribe',
          label: 'Whisper 转录',
          disabled: false,
          title: '用 faster-whisper 提取音频转文字',
        },
        {
          action: 'analyze',
          label: 'AI分析视频',
          disabled: true,
          title: '请先压缩视频，或切换到「压缩」视图后重跑分析',
        },
        {
          action: 'voiceover',
          label: '重跑口播文案',
          disabled: true,
          title: '请先压缩并完成分析，或切换到「压缩」视图',
        },
        {
          action: 'all',
          label: '重跑全部',
          disabled: true,
          title: '请先压缩视频，或切换到「压缩」视图',
        },
        { divider: true },
        {
          action: 'remove',
          label: '从项目移除',
          disabled: false,
          danger: true,
          title: '从项目中移除该视频',
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
        label: '压缩视频',
        disabled: true,
        title: '文件离线',
      },
      {
        action: 'analyze',
        label: 'AI分析视频',
        disabled: true,
        title: '文件离线',
      },
      {
        action: 'voiceover',
        label: '重跑口播文案',
        disabled: true,
        title: '文件离线',
      },
      {
        action: 'transcribe',
        label: 'Whisper 转录',
        disabled: true,
        title: '文件离线',
      },
      {
        action: 'all',
        label: '重跑全部',
        disabled: true,
        title: '文件离线',
      },
      { divider: true },
      {
        action: 'remove',
        label: '从项目移除',
        disabled: false,
        danger: true,
        title: '从项目视频列表移除对应原片（若能解析）',
      },
    ];
    return applyFfmpegMenuDeps(items, deps);
  }

  items = [
    {
      action: 'compress',
      label: '压缩视频',
      disabled: true,
      title: '当前为压缩结果；强制重压请到「原视频」视图或运行面板',
    },
    {
      action: 'analyze',
      label: 'AI分析视频',
      disabled: false,
      title: '调用 AI 重新分析视频内容',
    },
    {
      action: 'voiceover',
      label: '重跑口播文案',
      disabled: false,
      title: '基于分析结果，重新用 AI 生成口播解说文案',
    },
    {
      action: 'transcribe',
      label: 'Whisper 转录',
      disabled: false,
      title: '用 faster-whisper 对压缩视频转写语音',
    },
    {
      action: 'all',
      label: '重跑全部',
      disabled: false,
      title: '依次执行 AI 分析 + 口播文案',
    },
    { divider: true },
    {
      action: 'remove',
      label: '从项目移除',
      disabled: false,
      danger: true,
      title: '从项目视频列表移除对应原片（若能解析）',
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
