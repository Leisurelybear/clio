import { html } from './utils.js';
import { t } from './i18n.js';
import { buildRunStageModel, nextIncompleteStage } from './runner-stage-model.js';

export { buildRunStageModel, nextIncompleteStage };

function stageNamesText(names) {
  return (Array.isArray(names) ? names : [])
    .map(name => t(`run.step_short.${String(name)}`))
    .join(t('common.list_separator'));
}

function renderStageCounts(stage) {
  const pending = Number(stage.pending || 0);
  const pendingMax = Math.max(pending, Number(stage.pendingMax ?? pending) || 0);
  const skipped = Number(stage.skipped || 0);
  const skippedMin = Math.min(skipped, Number(stage.skippedMin ?? skipped) || 0);
  if (Number(stage.total) <= 0) return '';
  return html`<small>${t('run.guide.count_ranges', {
    run_min: pending,
    run_max: pendingMax,
    skip_min: skippedMin,
    skip_max: skipped,
  })}</small>`;
}

export function renderStageBoard(container, model, onOpenOutput) {
  if (!container) return;
  container.innerHTML = String(html`
    <h4>${t('run.guide.heading')}</h4>
    <ol class="run-stage-list">${model.map((stage, index) => html`
      <li class="run-stage-card ${stage.status}" data-stage="${stage.key}"
          aria-current="${stage.status === 'active' ? 'step' : 'false'}">
        <span class="run-stage-dot">${['done', 'reusable'].includes(stage.status) ? '✓' : index + 1}</span>
        <div class="run-stage-main">
          <div class="run-stage-row"><strong>${t(`run.stage.${stage.key}`)}</strong>
            <span>${t(`run.guide.status.${stage.status}`)}</span></div>
          <p class="run-stage-status-desc">${t(`run.guide.status_desc.${stage.status}`)}</p>
          <p>${t(`run.stage.${stage.key}.hint`)}</p>
          ${renderStageCounts(stage)}
          ${stage.cascadeFrom?.length
            ? html`<small class="run-stage-cascade">${t('run.preview.cascade_from', {
              steps: stageNamesText(stage.cascadeFrom),
            })}</small>` : ''}
          ${stage.waitingOn.length
            ? html`<small class="run-stage-waiting">${t(stage.status === 'waiting'
              ? 'run.guide.waiting_on' : 'run.guide.deferred_partial', {
              steps: stageNamesText(stage.waitingOn),
            })}</small>` : ''}
          ${stage.estimateIsMinimum
            ? html`<small class="run-stage-estimate-note">${t('run.guide.estimate_minimum')}</small>` : ''}
          ${stage.warnings.map(warning => html`<p class="warn">${warning}</p>`)}
          ${stage.blockers.length ? html`
            <div class="run-stage-blockers">
              <small>${t('run.guide.blockers_heading')}</small>
              <ul>${stage.blockers.map(reason => html`<li>${reason}</li>`)}</ul>
            </div>` : ''}
          ${stage.output && stage.status === 'reusable' ? html`<button class="btn-secondary run-stage-output" data-stage-output="${stage.key}">${t('run.guide.view_output')}</button>` : ''}
        </div>
      </li>`)}</ol>`);
  container.querySelectorAll('[data-stage-output]').forEach(button => {
    button.addEventListener('click', () => onOpenOutput?.(model.find(stage => stage.key === button.dataset.stageOutput)));
  });
}

export function renderCompletionActions(container, steps, onView, onReveal, outputPath, outputs = []) {
  if (!container) return;
  const hasTarget = steps.some(step => ['plan', 'plan_voiceover', 'voiceover', 'transcribe', 'analyze', 'compress', 'label'].includes(step));
  const outputRows = (Array.isArray(outputs) ? outputs : []).filter(item => item?.label);
  container.innerHTML = String(html`
    <p class="hint">${t('run.guide.completed_hint')}</p>
    <div class="run-outputs-card">
      <small>${t('run.guide.outputs_heading')}</small>
      ${outputRows.length ? html`<ul>${outputRows.map(item => html`<li><span>${item.label}</span>${item.detail ? html`<small>${item.detail}</small>` : ''}</li>`)}</ul>`
        : html`<p class="muted">${t('run.guide.outputs_empty')}</p>`}
    </div>
    <div class="run-done-actions">
      ${hasTarget ? html`<button id="btn-run-view-result" class="btn-primary">${t(steps.includes('plan') || steps.includes('plan_voiceover') ? 'run.guide.view_plan' : 'run.guide.view_result')}</button>` : ''}
      ${outputPath ? html`<button id="btn-run-reveal-output" class="btn-secondary">${t('run.guide.open_output')}</button>` : ''}
    </div>`);
  container.querySelector('#btn-run-view-result')?.addEventListener('click', onView);
  container.querySelector('#btn-run-reveal-output')?.addEventListener('click', onReveal);
}
