/**
 * Pure helpers for editor save / tab-switch guards.
 */

/**
 * Decide what Ctrl+S / Save should do for the current entity+tab.
 * @returns {{ action: 'texts'|'voiceover'|'plan'|'config'|'noop', configTab?: string, reasonKey?: string }}
 */
export function resolveEditorSaveTarget({ entity, tab, configTab } = {}) {
  if (entity === 'run' || entity === 'logs' || entity === 'tokens') {
    return { action: 'noop', reasonKey: 'editor.save.no_target' };
  }
  if (entity === 'config') {
    const ct = configTab || 'global';
    if (ct === 'global' || ct === 'project') {
      return { action: 'config', configTab: ct };
    }
    return { action: 'noop', reasonKey: 'plan.config_readonly' };
  }
  if (entity === 'plan') {
    return { action: 'plan' };
  }
  // video entity (default)
  if (tab === 'texts') return { action: 'texts' };
  if (tab === 'voiceover') return { action: 'voiceover' };
  return {
    action: 'noop',
    reasonKey: 'editor.save.nothing',
  };
}

/** True when switching editor tabs would drop unsaved in-memory edits if user continues. */
export function shouldConfirmDirtyTabSwitch({ dirty, fromTab, toTab } = {}) {
  if (!dirty) return false;
  if (!toTab || toTab === fromTab) return false;
  return true;
}
