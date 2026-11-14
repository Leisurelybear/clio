/** Plan artifact save, delete, and config persistence actions. */

import { api, apiWithMeta } from './api.js';
import { confirmDialog } from './confirm.js';
import { addToast } from './toast.js';
import {
  artifactIfMatchHeader,
  isArtifactConflict,
  resolveEditorSaveTarget,
} from './editor-save.js';
import { applyLocale, getLocale, t } from './i18n.js';
import { state, artifactVersion, setArtifactVersion, clearArtifactVersion } from './state.js';
import { clearDirty, setStatus, updateSidebarDay } from './utils.js';

export function configSaveStatusForTab(tab) {
  if (tab === 'project') {
    return { message: t('plan.config_saved_project'), level: 'ok' };
  }
  if (tab === 'global') {
    return {
      message: t('plan.config_saved_global'),
      level: 'ok',
    };
  }
  return { message: t('plan.config_readonly'), level: 'warn' };
}

/** Apply `ui.language` from a freshly saved global config without a reload. */
export function applySavedConfigLanguage(config = state.configGlobal) {
  const next = config?.ui?.language;
  if (!next || next === getLocale()) return getLocale();
  return applyLocale(next);
}

/**
 * Delete a plan, then select the next available plan or render the empty pane.
 * `render` stays injectable so this module does not import the editor-plan facade.
 */
export async function deletePlanArtifact(day = state.currentDay, options = {}) {
  const label = String(day || '');
  if (!label) return false;
  const ask = options.confirm || confirmDialog;
  if (!(await ask(t('plan.delete_plan_confirm', { day: label }), { danger: true }))) return false;
  const request = options.api || api;
  try {
    await request('DELETE', `/api/plan?day=${encodeURIComponent(label)}`);
  } catch (e) {
    const message = t('plan.delete_plan_failed', { detail: e.message });
    setStatus(message, 'err');
    addToast(message, 'error', 6000, { persist: false });
    return false;
  }
  clearDirty();
  state.plan = null;
  clearArtifactVersion('plan', label);
  state.planPreviewMedia = null;
  state.previewCutFallbacks?.clear();
  state._previewMedia = null;
  const message = t('plan.delete_plan_done', { day: label });
  setStatus(message, 'ok');
  addToast(message, 'success', undefined, { persist: false });

  const sidebar = options.sidebar || await import('./sidebar.js');
  await sidebar.loadPlans();
  if (state.availablePlans.length) {
    await sidebar.selectPlan(state.availablePlans[0].day_label);
  } else {
    state.currentDay = label;
    updateSidebarDay();
    sidebar.saveProject();
    options.render?.();
  }
  return true;
}

let _saveGeneration = 0;

/**
 * Save the active editor target with plan/artifact optimistic concurrency.
 * `onPlanSaved` lets the facade schedule plan readiness without importing it here.
 */
export async function saveEditorPlan(options = {}) {
  if (!state.dirty) {
    setStatus(t('plan.nothing_to_save'), 'warn');
    return;
  }
  const generation = ++_saveGeneration;
  const entity = state.currentEntity;
  const day = state.currentDay;
  const tab = state.currentTab;
  const videoFile = state.currentVideo;
  const planData = state.plan;
  const textsData = state.texts;
  const voiceoverData = state.voiceover;
  const target = resolveEditorSaveTarget({
    entity,
    tab,
    configTab: state.configTab,
  });
  if (target.action === 'noop') {
    setStatus(t(target.reasonKey || 'plan.no_save_target'), 'warn');
    return;
  }
  try {
    if (target.action === 'config') {
      const ct = target.configTab || 'global';
      let r;
      if (ct === 'global') {
        r = await api('PUT', '/api/config/global', state.configGlobal);
      } else {
        r = await api('PUT', '/api/config/project', state.configProject);
      }
      if (r.error) throw new Error(r.error);
      if (generation !== _saveGeneration) return;
      clearDirty();
      if (ct === 'global') applySavedConfigLanguage();
      const status = configSaveStatusForTab(ct);
      setStatus(status.message, status.level);
      addToast(status.message, status.level === 'ok' ? 'success' : 'warning', undefined, { persist: false });
      try {
        if (typeof window.refreshFfmpegDepsUi === 'function') {
          await window.refreshFfmpegDepsUi();
        } else {
          const { loadFfmpegDeps, renderVideoList } = await import('./sidebar-data.js');
          await loadFfmpegDeps();
          renderVideoList();
        }
      } catch { /* dependency refresh is best-effort */ }
      return;
    }
    let versionKind = null;
    let versionKey = null;
    let requestUrl = null;
    let requestBody = null;
    if (target.action === 'plan') {
      versionKind = 'plan';
      versionKey = day;
      requestUrl = `/api/plan?day=${day}`;
      requestBody = planData;
    } else if (target.action === 'texts') {
      const v = state.videos.find(x => x.file === videoFile);
      if (!v || !v.text_json) throw new Error(t('plan.no_texts_json'));
      versionKind = 'texts';
      versionKey = v.text_json;
      requestUrl = `/api/texts?file=${encodeURIComponent(v.text_json)}`;
      requestBody = textsData;
    } else if (target.action === 'voiceover') {
      const v = state.videos.find(x => x.file === videoFile);
      if (!v || !v.script_json) throw new Error(t('plan.no_voiceover_json'));
      versionKind = 'voiceover';
      versionKey = v.script_json;
      requestUrl = `/api/voiceover?file=${encodeURIComponent(v.script_json)}`;
      requestBody = voiceoverData;
    } else {
      setStatus(t('plan.no_save_target'), 'warn');
      return;
    }
    const expected = artifactVersion(versionKind, versionKey);
    const result = await apiWithMeta('PUT', requestUrl, requestBody, {
      headers: artifactIfMatchHeader(expected),
    });
    if (generation !== _saveGeneration) return;
    setArtifactVersion(versionKind, versionKey, result.version || result.data?.version);
    clearDirty();
    setStatus(t('plan.saved'), 'ok');
    addToast(t('plan.saved'), 'success', undefined, { persist: false });
    if (target.action === 'plan') options.onPlanSaved?.();
  } catch (e) {
    if (isArtifactConflict(e)) {
      const msg = t('plan.save_conflict');
      setStatus(msg, 'err');
      addToast(msg, 'error', 8000, { title: t('plan.save_conflict_title') });
      return;
    }
    const msg = t('plan.save_failed', { detail: e.message });
    setStatus(msg, 'err');
    addToast(msg, 'error', 6000, { title: t('plan.save_failed_title') });
  }
}