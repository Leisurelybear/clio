import { state } from './state.js';
import { $, markDirty, setStatus } from './utils.js';
import { api } from './api.js';
import { renderRefineUI, refineCurrentFile } from './editor-refine.js';
import { renderEmptyArtifactHtml, bindEmptyArtifactActions } from './empty-states.js';


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
          setStatus('请先选择视频', 'warn');
          return;
        }
        const vid = state.videos.find(x => x.file === file);
        setStatus(`正在重跑 ${task} (${file})...`, 'ok', { persist: false });
        try {
          const r = await api('POST', '/api/rerun', {
            video: file,
            task,
            source: state.source,
            index: vid?.index || undefined,
            abspath: vid?.abs_path || undefined,
          });
          if (r.ok) {
            setStatus(r.message || `${task} 已启动`, 'ok', { persist: false });
            import('./sidebar-rerun.js').then(mod => mod.showRerunProgress(task, file, r.task_id));
          } else {
            throw new Error(r.error || '重跑失败');
          }
        } catch (e) {
          setStatus('重跑失败: ' + e.message, 'err');
        }
      },
    });
    return;
  }
  pane.innerHTML = `
    <h3>口播文案</h3>
    <label>标题 <input data-field="title"></label>
    <label>口播文案 <textarea data-field="voiceover" rows="10"></textarea></label>
    <h3>剪辑提示</h3>
    <label>剪辑提示 <textarea data-field="edit_tip" rows="2"></textarea></label>
    <label>预计时长 (秒) <input data-field="duration_hint_sec" type="number" min="0" step="0.5"></label>
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
