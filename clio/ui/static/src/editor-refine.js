import { state, setArtifactVersion } from './state.js';
import { $, escapeHtml, setStatus } from './utils.js';
import { api } from './api.js';
import { t } from './i18n.js';
import { confirmDialog } from './confirm.js';


export function renderRefineUI(type) {
  const isRefining = state.refining?.type === type;
  const btnText = isRefining ? t('editor.refine.running') : t('editor.refine.button');
  const statusHtml = isRefining
    ? `<span class="loading">${t('editor.refine.loading')}</span>`
    : state._refineError && !state.refining
      ? `<span class="err">${escapeHtml(state._refineError)}</span>`
      : '';
  return `
    <hr>
    <h3>${t('editor.refine.button')}</h3>
    <label>${t('editor.refine.context_label')}<textarea id="refine-context-${type}" rows="2" placeholder="${t('editor.refine.context_placeholder')}"></textarea></label>
    <button id="btn-refine-${type}" class="btn btn-secondary"${isRefining ? ' disabled' : ''}>${btnText}</button>
    <p id="refine-status-${type}" class="status">${statusHtml}</p>
  `;
}


export async function refineCurrentFile(type) {
  const v = state.videos.find(x => x.file === state.currentVideo);
  if (!v) return;
  const fileField = type === 'texts' ? 'text_json' : 'script_json';
  if (!v[fileField]) { setStatus(t(type === 'texts' ? 'plan.no_texts_json' : 'plan.no_voiceover_json'), 'err'); return; }
  const label = type === 'texts' ? t('editor.artifact.texts') : t('editor.artifact.voiceover');
  const saveCurrentArtifact = state.dirty && state.currentEntity === 'video' && state.currentTab === type;
  const confirmKey = saveCurrentArtifact ? 'editor.refine.confirm_dirty' : 'editor.refine.confirm';
  if (!(await confirmDialog(t(confirmKey, { label })))) return;
  const ctx = $(`refine-context-${type}`).value.trim();
  if (state.refining) return;

  if (saveCurrentArtifact) {
    const { save } = await import('./editor-plan.js');
    await save();
    if (state.dirty) return;
  }

  state._refineError = null;
  const refineFile = v[fileField];
  state.refining = { type, file: refineFile };

  const { renderActiveTab, renderTexts, renderVoiceover } = await import('./editor.js');
  renderActiveTab();

  try {
    const r = await api('POST', '/api/refine', { file: refineFile, type, context: ctx || undefined });
    if (r.error) throw new Error(r.error);
    if (type === 'texts') state.texts = r.data;
    else state.voiceover = r.data;
    setArtifactVersion(type, refineFile, r.version);
    if (state.refining?.file === refineFile) state.refining = null;
    if (state.currentVideo === v.file && state.currentTab === type) {
      if (type === 'texts') renderTexts();
      else renderVoiceover();
    }
    setStatus(t('editor.refine.done', { label }), 'ok');
  } catch (e) {
    if (state.refining?.file === refineFile) {
      if (e.body?.code === 'refine_busy' || e.message.includes('正在 AI 审阅')) {
        state._refineError = t('editor.refine.busy');
      } else {
        state._refineError = t('editor.refine.failed', { detail: e.message });
      }
      setStatus(state._refineError, 'err');
      state.refining = null;
      if (state.currentVideo === v.file && state.currentTab === type) renderActiveTab();
    }
  }
}
