import { state } from './state.js';
import { $, markDirty, setStatus } from './utils.js';
import { api } from './api.js';
import { renderRefineUI, refineCurrentFile } from './editor-refine.js';
import { renderEmptyArtifactHtml, bindEmptyArtifactActions } from './empty-states.js';
import { t } from './i18n.js';


export function renderVoiceover() {
  const v = state.voiceover;
  const pane = $('tab-voiceover');
  if (!state.currentVideo) {
    pane.innerHTML = renderEmptyArtifactHtml('voiceover', { hasVideo: false });
    return;
  }
  if (!v) {
    pane.innerHTML = renderEmptyArtifactHtml('voiceover', {
      hasVideo: true,
      hasTexts: !!state.texts,
    });
    bindEmptyArtifactActions(pane, {
      onRun: () => import('./sidebar.js').then(m => m.goToRunTab()),
      onRerun: async (task) => {
        const file = state.currentVideo;
        if (!file) {
          setStatus(t('editor.pick_video_first'), 'warn');
          return;
        }
        const vid = state.videos.find(x => x.file === file);
        setStatus(t('sidebar.rerunning', { task, file }), 'ok', { persist: false });
        try {
          const r = await api('POST', '/api/rerun', {
            video: file,
            task,
            source: state.source,
            index: vid?.index || undefined,
            abspath: vid?.abs_path || undefined,
          });
          if (r.ok) {
            setStatus(r.message || t('sidebar.rerun_started', { task }), 'ok', { persist: false });
            import('./sidebar-rerun.js').then(mod => mod.showRerunProgress(task, file, r.task_id));
          } else {
            throw new Error(r.error || t('sidebar.rerun_failed'));
          }
        } catch (e) {
          setStatus(t('sidebar.rerun_failed_detail', { detail: e.message }), 'err');
        }
      },
    });
    return;
  }
  pane.innerHTML = `
    <h3>${t('editor.artifact.voiceover')}</h3>
    <label>${t('plan.field.title')} <input data-field="title"></label>
    <label>${t('editor.artifact.voiceover')} <textarea data-field="voiceover" rows="10"></textarea></label>
    <h3>${t('editor.field.edit_tip')}</h3>
    <label>${t('editor.field.edit_tip')} <textarea data-field="edit_tip" rows="2"></textarea></label>
    <label>${t('editor.field.duration_hint')} <input data-field="duration_hint_sec" type="number" min="0" step="0.5"></label>
  `;
  for (const k of ['title', 'voiceover', 'edit_tip']) {
    const el = pane.querySelector(`[data-field="${k}"]`);
    el.value = v[k] || '';
    el.oninput = () => { v[k] = el.value; markDirty(); };
  }
  const dEl = pane.querySelector('[data-field="duration_hint_sec"]');
  dEl.value = v.duration_hint_sec ?? '';
  dEl.oninput = () => { v.duration_hint_sec = parseFloat(dEl.value) || 0; markDirty(); };
  pane.insertAdjacentHTML('beforeend', renderRefineUI('scripts'));
  pane.querySelector(`#btn-refine-scripts`).onclick = () => refineCurrentFile('scripts');
}
