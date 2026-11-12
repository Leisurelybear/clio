/** Subtitle placeholder resolution for plan segment editing. */

import { apiWithMeta } from './api.js';
import { state, setArtifactVersion } from './state.js';
import { t } from './i18n.js';

/**
 * Prefill a segment's subtitle textarea with the saved plan subtitle, or fall
 * back to the video's voiceover text as a starting point (read from it, never
 * written back). Editing only updates the plan's subtitle field.
 * @param {HTMLElement} li
 * @param {object} seg
 */
export function prefillPlanSegmentSubtitle(li, seg) {
  const subEl = li.querySelector('[data-k="subtitle"]');
  if (!subEl) return;
  const planText = typeof seg.subtitle === 'string' ? seg.subtitle.trim() : '';
  if (planText) {
    subEl.value = planText;
    subEl.placeholder = '';
    return;
  }
  const v = state.videos.find((x) => String(x.index) === String(seg.index));
  if (!v) { subEl.placeholder = t('plan.subtitle.no_video'); return; }
  if (!v.script_json) { subEl.placeholder = t('plan.subtitle.no_script'); return; }
  apiWithMeta('GET', `/api/voiceover?file=${encodeURIComponent(v.script_json)}`)
    .then(({ data, version }) => {
      setArtifactVersion('voiceover', v.script_json, version);
      const text = typeof data?.voiceover === 'string' ? data.voiceover : '';
      subEl.placeholder = text || t('plan.subtitle.none');
    })
    .catch(() => { subEl.placeholder = t('plan.subtitle.load_failed'); });
}