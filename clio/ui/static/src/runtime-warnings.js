import { registerNotification } from './notification-center.js';
import { state } from './state.js';

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '']);
const WARNING_STATE_KEY = 'clio.runtime-warning-state.v1';

function isLocalHost(hostname) {
  return LOCAL_HOSTS.has(String(hostname || '').toLowerCase());
}

function warningScope() {
  return String(state.currentProjectDir || state.currentProjectName || 'global');
}

function warningOccurrences(warnings) {
  let stored = {};
  try {
    const parsed = JSON.parse(localStorage.getItem(WARNING_STATE_KEY) || '{}');
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) stored = parsed;
  } catch { /* ignore malformed browser state */ }
  const scope = warningScope();
  const prefix = `${scope}:`;
  const activeKeys = new Set(warnings.map(warning => `${scope}:${warning.id}`));
  Object.keys(stored).forEach((key) => {
    if (key.startsWith(prefix) && !activeKeys.has(key)) stored[key].active = false;
  });
  const occurrences = new Map();
  for (const warning of warnings) {
    const key = `${scope}:${warning.id}`;
    const previous = stored[key] || { occurrence: 0, active: false, text: '' };
    const changed = previous.text && previous.text !== warning.text;
    const occurrence = previous.occurrence + (previous.active && !changed ? 0 : 1);
    stored[key] = { occurrence, active: true, text: warning.text, seen_at: Date.now() };
    occurrences.set(warning.id, occurrence);
  }
  const entries = Object.entries(stored).sort((a, b) => (b[1]?.seen_at || 0) - (a[1]?.seen_at || 0));
  stored = Object.fromEntries(entries.slice(0, 200));
  try { localStorage.setItem(WARNING_STATE_KEY, JSON.stringify(stored)); } catch { /* best effort */ }
  return occurrences;
}

/**
 * Build runtime banner warnings (config / host / orphaned cut backups).
 * @param {{ config?: object, hostname?: string, hasToken?: boolean, orphanedCutBackups?: Array,
 *           ffmpegDeps?: object|null, missingKeys?: Array }} opts
 */
function buildRuntimeWarnings({
  config = {},
  hostname = '',
  hasToken = false,
  orphanedCutBackups = null,
  ffmpegDeps = null,
  missingKeys = null,
} = {}) {
  const warnings = [];
  const debugPrintPrompt = Boolean(config?.ai?.debug_print_prompt);
  if (debugPrintPrompt) {
    warnings.push({
      id: 'debug-prompt',
      level: 'warning',
      text: 'ai.debug_print_prompt=true：AI 调用会把完整 prompt 写入日志/控制台，可能包含行程上下文或临时指令。',
    });
  }

  if (Array.isArray(missingKeys) && missingKeys.length > 0) {
    const names = missingKeys.map((k) => k.provider || '').filter(Boolean).join('、');
    warnings.push({
      id: 'deps-keys-missing',
      level: 'danger',
      text: `AI 任务缺少 API 密钥（${names}）。请在设置 → Provider 中配置密钥，否则 AI 任务会失败。`,
      action: { id: 'go-settings-keys', label: '去设置' },
    });
  }

  if (!isLocalHost(hostname)) {
    if (hasToken) {
      warnings.push({
        id: 'lan-host',
        level: 'warning',
        text: `当前通过 ${hostname} 访问 UI，服务可能暴露在局域网内。`,
      });
    } else {
      warnings.push({
        id: 'lan-no-token',
        level: 'danger',
        text: `当前通过 ${hostname} 访问 UI，且浏览器没有 API token。建议启用 token 后再在局域网访问。`,
      });
    }
  }

  const orphans = Array.isArray(orphanedCutBackups) ? orphanedCutBackups : [];
  if (orphans.length > 0) {
    const sample = orphans
      .slice(0, 3)
      .map((o) => o.name || o.target || '')
      .filter(Boolean)
      .join('、');
    const more = orphans.length > 3 ? ` 等 ${orphans.length} 个` : '';
    warnings.push({
      id: 'cut-orphaned-bak',
      level: 'warning',
      text:
        `检测到未完成的裁剪覆盖备份（*.clio_bak）${sample ? `：${sample}${more}` : `（${orphans.length} 个）`}。` +
        '中断的重剪可能留下旧文件备份；可一键恢复为覆盖前的视频。',
      action: { id: 'restore-cut-backups', label: '恢复旧文件' },
    });
  }

  if (ffmpegDeps && ffmpegDeps.ok === false) {
    warnings.push({
      id: 'ffmpeg-missing',
      level: 'warning',
      text:
        ffmpegDeps.detail ||
        '未找到 ffmpeg/ffprobe。压缩 / 裁剪 / 转录抽音 / 波形等功能不可用。请安装 ffmpeg 或配置 paths.ffmpeg。',
      action: { id: 'show-ffmpeg-help', label: '如何安装' },
    });
  }

  return warnings;
}

function renderRuntimeWarnings(container, warnings, handlers = {}) {
  if (!container) return;
  container.replaceChildren();
  container.hidden = warnings.length === 0;
  for (const warning of warnings) {
    const item = document.createElement('div');
    item.className = `runtime-warning ${warning.level}`;
    item.dataset.warningId = warning.id;

    const text = document.createElement('span');
    text.className = 'runtime-warning-text';
    text.textContent = warning.text;
    item.appendChild(text);

    if (warning.action?.id) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'runtime-warning-action';
      btn.textContent = warning.action.label || '处理';
      btn.dataset.actionId = warning.action.id;
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        handlers.onAction?.(warning.action.id, warning);
      });
      item.appendChild(btn);
    }

    container.appendChild(item);
  }
}

/**
 * Refresh banner: static config/host warnings + optional orphaned cut bak scan.
 * @param {object} config
 * @param {{ orphanedCutBackups?: Array|null, onAction?: (id: string, warning: object) => void }} [opts]
 */
function updateRuntimeWarnings(config, opts = {}) {
  const container = document.getElementById('runtime-warnings');
  const warnings = buildRuntimeWarnings({
    config,
    hostname: window.location.hostname,
    hasToken: Boolean(sessionStorage.getItem('api_token')),
    orphanedCutBackups: opts.orphanedCutBackups,
    ffmpegDeps: opts.ffmpegDeps ?? null,
    missingKeys: opts.missingKeys ?? null,
  });
  const occurrences = warningOccurrences(warnings);
  warnings.forEach((warning) => {
    const occurrence = occurrences.get(warning.id) || 1;
    registerNotification({
      message: warning.text,
      severity: warning.level === 'danger' ? 'error' : 'warning',
      title: '运行环境提醒',
      sourceType: 'runtime_warning',
      sourceId: `${warning.id}:${occurrence}`,
      dedupeKey: `runtime-warning:${warningScope()}:${warning.id}:${occurrence}:${warning.text}`,
      data: {
        action_id: warning.action?.id || null,
        warning_id: warning.id,
      },
    });
  });
  renderRuntimeWarnings(container, warnings, { onAction: opts.onAction });
}

export { buildRuntimeWarnings, renderRuntimeWarnings, updateRuntimeWarnings, warningOccurrences };
