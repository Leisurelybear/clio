import { registerNotification } from './notification-center.js';
import { state } from './state.js';
import { hasKey, onLocaleChange, t } from './i18n.js';

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '']);
const WARNING_STATE_KEY = 'clio.runtime-warning-state.v1';

function taskLabel(name) {
  const key = `settings.task.label.${name}`;
  return hasKey(key) ? t(key) : name;
}

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
 *           ffmpegDeps?: object|null, missingKeys?: Array, unboundTasks?: Array }} opts
 */
function buildRuntimeWarnings({
  config = {},
  hostname = '',
  hasToken = false,
  orphanedCutBackups = null,
  ffmpegDeps = null,
  missingKeys = null,
  unboundTasks = null,
} = {}) {
  const warnings = [];
  const debugPrintPrompt = Boolean(config?.ai?.debug_print_prompt);
  if (debugPrintPrompt) {
    warnings.push({
      id: 'debug-prompt',
      level: 'warning',
      text: t('runtime.warn.debug_prompt'),
    });
  }

  if (Array.isArray(unboundTasks) && unboundTasks.length > 0) {
    const names = unboundTasks
      .map((name) => taskLabel(name))
      .filter(Boolean)
      .join(t('runtime.list_separator'));
    warnings.push({
      id: 'ai-tasks-unbound',
      level: 'danger',
      text: t('runtime.warn.ai_tasks_unbound', { names }),
      action: { id: 'go-settings-tasks', label: t('runtime.action.go_global_binding') },
    });
  }

  if (Array.isArray(missingKeys) && missingKeys.length > 0) {
    const names = missingKeys.map((k) => k.provider || '').filter(Boolean).join(t('runtime.list_separator'));
    warnings.push({
      id: 'deps-keys-missing',
      level: 'danger',
      text: t('runtime.warn.deps_keys_missing', { names }),
      action: { id: 'go-settings-keys', label: t('runtime.action.go_settings') },
    });
  }

  if (!isLocalHost(hostname)) {
    if (hasToken) {
      warnings.push({
        id: 'lan-host',
        level: 'warning',
        text: t('runtime.warn.lan_host', { hostname }),
      });
    } else {
      warnings.push({
        id: 'lan-no-token',
        level: 'danger',
        text: t('runtime.warn.lan_no_token', { hostname }),
      });
    }
  }

  const orphans = Array.isArray(orphanedCutBackups) ? orphanedCutBackups : [];
  if (orphans.length > 0) {
    const sample = orphans
      .slice(0, 3)
      .map((o) => o.name || o.target || '')
      .filter(Boolean)
      .join(t('runtime.list_separator'));
    const more = orphans.length > 3 ? ` ${t('runtime.warn.orphan_more', { count: orphans.length })}` : '';
    warnings.push({
      id: 'cut-orphaned-bak',
      level: 'warning',
      text: sample
        ? t('runtime.warn.cut_orphaned_bak_sample', { sample: `${sample}${more}` })
        : t('runtime.warn.cut_orphaned_bak_count', { count: orphans.length }),
      action: { id: 'restore-cut-backups', label: t('runtime.action.restore_backups') },
    });
  }

  if (ffmpegDeps && ffmpegDeps.ok === false) {
    warnings.push({
      id: 'ffmpeg-missing',
      level: 'warning',
      text: ffmpegDeps.detail || t('runtime.warn.ffmpeg_missing'),
      action: { id: 'show-ffmpeg-help', label: t('runtime.action.how_to_install') },
    });
  }

  return warnings;
}

let _lastRender = null;

function renderRuntimeWarnings(container, warnings, handlers = {}) {
  if (!container) return;
  _lastRender = { container, warnings, handlers };
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
      btn.textContent = warning.action.label || t('runtime.action.default');
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
    unboundTasks: opts.unboundTasks ?? null,
  });
  const occurrences = warningOccurrences(warnings);
  warnings.forEach((warning) => {
    const occurrence = occurrences.get(warning.id) || 1;
    registerNotification({
      message: warning.text,
      severity: warning.level === 'danger' ? 'error' : 'warning',
      title: t('runtime.warn.title'),
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

onLocaleChange(() => {
  if (_lastRender?.container) {
    renderRuntimeWarnings(_lastRender.container, _lastRender.warnings, _lastRender.handlers);
  }
});

export { buildRuntimeWarnings, renderRuntimeWarnings, updateRuntimeWarnings, warningOccurrences };
