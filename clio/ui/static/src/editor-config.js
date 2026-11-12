/**
 * Compatibility facade for the decomposed config editor modules.
 */
import {
  attachProviderListHandlers,
  renderProviderList,
  renderTagInput,
  testProvider,
} from './editor-providers.js';
import { attachTaskBindingHandlers, renderTaskBinding } from './editor-task-binding.js';
import { renderLogs, stopLogsPolling } from './editor-logs.js';
import { renderTokens } from './editor-tokens.js';
import { renderPromptManagement } from './editor-prompts.js';
import { initProjectConfig, renderConfig } from './editor-config-page.js';
import { _renderTooltip } from './editor-config-form.js';

export { initProjectConfig, renderConfig };
export { renderLogs, renderTokens, stopLogsPolling };
export { providerEnvName, providerTypeLabel } from './editor-providers.js';
export { mergeTaskBindings, taskDescription, taskLabel } from './editor-task-binding.js';
export { renderPromptManagement as _renderPromptManagement };
export {
  FIELD_LABELS,
  descFromPath,
  labelFromPath,
  optionLabel,
  pathPickKind,
} from './editor-config-form.js';
export {
  _attachConfigForm,
  _attachContextTemplate,
  _isAdvancedVisible,
  _loadConfigSchema,
  _renderConfigForm,
  _renderConfigGlobal,
  _renderConfigProject,
  _renderTooltip,
  _setAdvancedVisible,
} from './editor-config-form.js';

export function _renderProviderList(providers, descs) {
  return renderProviderList(providers, descs);
}

export function _renderTagInput(container, values, onChange) {
  return renderTagInput(container, values, onChange);
}

export function _testProvider(providersObj, providerName) {
  return testProvider(providersObj, providerName);
}

export function _attachProviderListHandlers(pane, providersObj) {
  attachProviderListHandlers(pane, providersObj, renderConfig);
}

export function _renderTaskBinding(tasks, providersObj, descs, opts = {}) {
  return renderTaskBinding(tasks, providersObj, descs, opts, _renderTooltip);
}

export function _attachTaskBindingHandlers(pane, cfg, opts = {}) {
  attachTaskBindingHandlers(pane, cfg, opts, renderConfig);
}