import { state } from './state.js';
import { $, escapeHtml, setStatus } from './utils.js';
import { api } from './api.js';


export function renderRefineUI(type) {
  const isRefining = state.refining?.type === type;
  const btnText = isRefining ? '⏳ AI 审阅中...' : 'AI 审阅修正';
  const statusHtml = isRefining
    ? '<span class="loading">正在请求 AI，请稍候...</span>'
    : state._refineError && !state.refining
      ? `<span class="err">${escapeHtml(state._refineError)}</span>`
      : '';
  return `
    <hr>
    <h3>AI 审阅修正</h3>
    <label>临时上下文（可选，如更正建议）<textarea id="refine-context-${type}" rows="2" placeholder="例如：地名写错了，请修正为正确的拼音"></textarea></label>
    <button id="btn-refine-${type}" class="btn btn-secondary"${isRefining ? ' disabled' : ''}>${btnText}</button>
    <p id="refine-status-${type}" class="status">${statusHtml}</p>
  `;
}


export async function refineCurrentFile(type) {
  const v = state.videos.find(x => x.file === state.currentVideo);
  if (!v) return;
  const fileField = type === 'texts' ? 'text_json' : 'script_json';
  if (!v[fileField]) { setStatus(`当前视频没有 ${type} JSON`, 'err'); return; }
  const label = type === 'texts' ? '素材分析' : '口播文案';
  if (!confirm(`确定要提交 AI 审阅修正「${label}」吗？当前未保存的修改将丢失。`)) return;
  const ctx = $(`refine-context-${type}`).value.trim();
  if (state.refining) return;

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
    if (state.refining?.file === refineFile) state.refining = null;
    if (state.currentVideo === v.file && state.currentTab === type) {
      if (type === 'texts') renderTexts();
      else renderVoiceover();
    }
    setStatus(`${label}已 AI 审阅修正`, 'ok');
  } catch (e) {
    if (state.refining?.file === refineFile) {
      if (e.message.includes('正在 AI 审阅')) {
        state._refineError = '该文件正在 AI 审阅中（其他页面已触发），请等待完成';
      } else {
        state._refineError = `修正失败: ${e.message}`;
      }
      setStatus(state._refineError, 'err');
      state.refining = null;
      if (state.currentVideo === v.file && state.currentTab === type) renderActiveTab();
    }
  }
}
