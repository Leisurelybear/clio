import { state } from './state.js';
import { $, setStatus } from './utils.js';
import { api } from './api.js';
import { t } from './i18n.js';
import {
  renderSubtitleSettingsPanel,
  mergeSubtitleSettings,
  serializeLatestWrites,
} from './subtitle-settings.js';
import { renderPlanSubtitleFromState } from './plan-subtitle.js';

let _subtitleSettingsFrame = 0;

/** Keep the plan accordion aligned with the active preview segment. */
export function syncPlanExpandFromPreview() {
  if (state.previewIndex < 0) return;
  // Don't collapse/switch expand while the user is mid-edit in another row
  // (full renderPlan would destroy focus and IME composition).
  const ae = document.activeElement;
  const planList = document.getElementById('plan-list');
  if (
    ae
    && planList
    && planList.contains(ae)
    && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')
  ) {
    const editingLi = ae.closest('[data-preview-index]');
    const editingIdx = editingLi ? parseInt(editingLi.dataset.previewIndex, 10) : NaN;
    if (Number.isFinite(editingIdx) && editingIdx !== state.previewIndex) {
      // Catch up expand once the user leaves the field (do not leave accordion
      // permanently lagging previewIndex).
      if (!ae.dataset.planExpandResyncBound) {
        ae.dataset.planExpandResyncBound = '1';
        ae.addEventListener('blur', () => {
          delete ae.dataset.planExpandResyncBound;
          // Defer past focus move so activeElement is no longer this field
          setTimeout(() => syncPlanExpandFromPreview(), 0);
        }, { once: true });
      }
      return;
    }
  }
  import('./editor-plan.js').then((mod) => {
    if (typeof mod.setPlanExpandedIndex === 'function') {
      mod.setPlanExpandedIndex(state.previewIndex);
    }
  }).catch(() => { /* ignore */ });
}

/** Fingerprint of plan video indexes + source for waveform reload decisions. */
export function planIndexesKey(tl) {
  const idxs = [...new Set(
    (tl?.segments || []).map((s) => String(s.videoIndex || '')).filter(Boolean),
  )].sort();
  return `${state.source || 'compressed'}|${idxs.join(',')}`;
}

export function renderPlanSubtitleSettings() {
  const wrap = $('subtitle-settings-wrap');
  if (!wrap) return;
  if (state.currentEntity !== 'plan') { wrap.innerHTML = ''; return; }
  _subtitleSettingsFrame += 1;
  const frame = _subtitleSettingsFrame;
  const project = state.configProject || {};
  // Latest-write-wins serializer: concurrent/pending saves cannot land out of
  // order, and only the freshest panel render may update UI after completion.
  const save = serializeLatestWrites((merged) => api('PUT', '/api/config/project', merged));
  renderSubtitleSettingsPanel(wrap, {
    config: project,
    onChange: (updates) => {
      const merged = mergeSubtitleSettings(state.configProject, updates);
      state.configProject = merged;
      save(merged).then(() => {
        if (frame === _subtitleSettingsFrame && state.currentEntity === 'plan') {
          setStatus(t('viewer.subtitle_style_saved'), 'ok');
          renderPlanSubtitleFromState();
        }
      }).catch(() => {
        if (frame === _subtitleSettingsFrame && state.currentEntity === 'plan') {
          setStatus(t('viewer.subtitle_style_save_failed'), 'warn');
        }
      });
    },
  });
}
