import { state } from './state.js';
import { setStatus, clearDirty } from './utils.js';
import { api } from './api.js';
import { addToast } from './toast.js';
import { confirmDialog } from './confirm.js';
import { updateRuntimeWarnings } from './runtime-warnings.js';
import { openFfmpegInstallDialog } from './ffmpeg-install.js';
import { t } from './i18n.js';

export function createRuntimeWarningsController({ loadFfmpegDeps, renderVideoList, selectConfig }) {
  let _orphanedCutBackups = [];
  let _missingKeys = null;
  let _unboundTasks = [];

  async function refreshRuntimeWarningsBanner() {
    try {
      const r = await api('GET', '/api/deps/keys');
      _missingKeys = Array.isArray(r.missing) ? r.missing : [];
      _unboundTasks = Array.isArray(r.unbound_tasks) ? r.unbound_tasks : [];
    } catch { /* keep the last successful probe during a transient outage */ }
    try {
      const r = await api('GET', '/api/cut/orphaned-backups');
      _orphanedCutBackups = Array.isArray(r.items) ? r.items : [];
    } catch { /* keep the last successful probe during a transient outage */ }
    updateRuntimeWarnings(state.config, {
      orphanedCutBackups: _orphanedCutBackups,
      ffmpegDeps: state.deps,
      missingKeys: _missingKeys,
      unboundTasks: _unboundTasks,
      onAction: handleRuntimeWarningAction,
    });
  }

  /** Re-probe ffmpeg, refresh banner (keeps orphan warnings), re-render menus. */
  async function refreshFfmpegDepsUi() {
    await loadFfmpegDeps();
    await refreshRuntimeWarningsBanner();
    renderVideoList();
    return state.deps;
  }
  async function handleRuntimeWarningAction(actionId) {
    if (actionId === 'show-ffmpeg-help') {
      showFfmpegHelp();
      return;
    }
    if (actionId === 'go-settings-keys') {
      await selectConfig();
      state.configTab = 'global';
      clearDirty();
      const { renderConfig } = await import('./editor-config.js');
      renderConfig();
      return;
    }
    if (actionId === 'go-settings-tasks') {
      await selectConfig();
      state.configTab = 'global';
      clearDirty();
      const { renderConfig } = await import('./editor-config.js');
      renderConfig();
      requestAnimationFrame(() => {
        document.querySelector('[data-section-key="global.task-binding"]')?.scrollIntoView({ block: 'start' });
      });
      return;
    }
    if (actionId !== 'restore-cut-backups') return;
    if (!_orphanedCutBackups.length) {
      setStatus(t('cut_backup.none'), 'warn');
      return;
    }
    const n = _orphanedCutBackups.length;
    const conflicts = (_orphanedCutBackups || []).filter((it) => it.conflict === 'true' || it.conflict === true);
    if (!(await confirmDialog(t('cut_backup.restore_confirm', { count: n })))) {
      return;
    }
    try {
      let body = {};
      if (conflicts.length) {
        const keepNew = await confirmDialog(t('cut_backup.conflict_confirm', { count: conflicts.length }));
        body = { keep_target: keepNew };
      }
      const r = await api('POST', '/api/cut/restore-backups', body);
      const count = r.count || (r.restored || []).length || 0;
      const errN = (r.errors || []).length;
      setStatus(
        errN ? t('cut_backup.restored_failed', { count, failed: errN }) : t('cut_backup.processed', { count }),
        errN ? 'warn' : 'ok',
      );
      addToast(
        errN ? t('cut_backup.toast_failed', { count, failed: errN }) : t('cut_backup.toast_processed', { count }),
        errN ? 'warning' : 'success',
        undefined,
        { persist: false },
      );
    } catch (e) {
      if (e.status === 409 && e.body && e.body.conflicts && e.body.conflicts.length) {
        const keepNew = await confirmDialog(t('cut_backup.conflict_confirm', { count: e.body.conflicts.length }));
        try {
          const r = await api('POST', '/api/cut/restore-backups', { keep_target: keepNew });
          const count = r.count || (r.restored || []).length || 0;
          setStatus(t('cut_backup.processed', { count }), 'ok');
          addToast(t('cut_backup.toast_processed', { count }), 'success', undefined, { persist: false });
        } catch (e2) {
          setStatus(t('cut_backup.restore_failed', { detail: e2.message }), 'err');
          addToast(t('cut_backup.restore_failed', { detail: e2.message }), 'error', 6000, { title: t('cut_backup.restore_failed_title') });
        }
      } else {
        setStatus(t('cut_backup.restore_failed', { detail: e.message }), 'err');
        addToast(t('cut_backup.restore_failed', { detail: e.message }), 'error', 6000, { title: t('cut_backup.restore_failed_title') });
      }
    }
    await refreshRuntimeWarningsBanner();
  }

  /** Show the managed installer with manual fallback guidance. */
  function showFfmpegHelp() {
    openFfmpegInstallDialog({ refreshDeps: refreshFfmpegDepsUi });
  }

  return {
    refreshRuntimeWarningsBanner,
    refreshFfmpegDepsUi,
    handleRuntimeWarningAction,
  };
}
