import { html } from './utils.js';
import { t } from './i18n.js';
import { buildRunStageModel, nextIncompleteStage } from './runner-stage-model.js';

export { buildRunStageModel, nextIncompleteStage };

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
          <p>${t(`run.stage.${stage.key}.hint`)}</p>
          ${stage.total > 0 ? html`<small>${t('run.guide.counts', { pending: stage.pending, skipped: stage.skipped })}</small>` : ''}
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

export function renderCompletionActions(container, steps, onView, onReveal, outputPath) {
  if (!container) return;
  const hasTarget = steps.some(step => ['plan', 'plan_voiceover', 'voiceover', 'transcribe', 'analyze', 'compress', 'label'].includes(step));
  container.innerHTML = String(html`
    <p class="hint">${t('run.guide.completed_hint')}</p>
    <div class="run-done-actions">
      ${hasTarget ? html`<button id="btn-run-view-result" class="btn-primary">${t(steps.includes('plan') || steps.includes('plan_voiceover') ? 'run.guide.view_plan' : 'run.guide.view_result')}</button>` : ''}
      ${outputPath ? html`<button id="btn-run-reveal-output" class="btn-secondary">${t('run.guide.open_output')}</button>` : ''}
    </div>`);
  container.querySelector('#btn-run-view-result')?.addEventListener('click', onView);
  container.querySelector('#btn-run-reveal-output')?.addEventListener('click', onReveal);
}
