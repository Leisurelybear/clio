import { state } from './state.js';
import { $$ } from './utils.js';

// Sub-module imports
import { renderTexts, renderTranscript } from './editor-texts.js';
import { renderVoiceover } from './editor-voiceover.js';
import { renderPlan, executeCut, save } from './editor-plan.js';
import {
  renderConfig, initProjectConfig, renderLogs, stopLogsPolling, renderTokens,
  _renderConfigForm, labelFromPath, _renderTooltip,
} from './editor-config.js';
import { renderRefineUI, refineCurrentFile } from './editor-refine.js';
import { renderTasks } from './task-center.js';


export function renderActiveTab() {
  if (state.currentEntity !== 'logs') {
    stopLogsPolling();
  }
  if (state.currentEntity === 'plan') {
    renderPlan();
    return;
  }
  if (state.currentEntity === 'run') {
    import('./runner.js').then(mod => mod.renderRun());
    return;
  }
  if (state.currentEntity === 'config') {
    renderConfig();
    return;
  }
  if (state.currentEntity === 'logs') {
    renderLogs();
    return;
  }
  if (state.currentEntity === 'tokens') {
    renderTokens();
    return;
  }
  if (state.currentEntity === 'tasks') {
    renderTasks();
    return;
  }
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === state.currentTab));
  $$('.tab-pane').forEach(p => p.classList.toggle('active', p.id === `tab-${state.currentTab}`));
  if (state.currentTab === 'texts') renderTexts();
  else if (state.currentTab === 'voiceover') renderVoiceover();
  else if (state.currentTab === 'transcript') renderTranscript();
}

// Re-export all public symbols so existing import paths continue to work
export {
  renderTexts,
  renderTranscript,
  renderVoiceover,
  renderRefineUI,
  refineCurrentFile,
  renderPlan,
  executeCut,
  save,
  renderConfig,
  initProjectConfig,
  renderLogs,
  stopLogsPolling,
  renderTokens,
  renderTasks,
  _renderConfigForm,
  labelFromPath,
  _renderTooltip,
};
