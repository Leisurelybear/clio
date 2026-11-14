/** Presentation-only groups. Execution still uses the pipeline's step order. */
export const RUN_STAGES = [
  { key: 'source_prep', steps: ['compress', 'transcribe'] },
  { key: 'ai_analysis', steps: ['analyze'] },
  { key: 'plan_alignment', steps: ['consistency', 'voiceover', 'plan'] },
  { key: 'voiceover_delivery', steps: ['plan_voiceover', 'label'] },
];

export function stageForStep(step) {
  return RUN_STAGES.find(stage => stage.steps.includes(step)) || null;
}

export function buildRunStageModel({ selectedSteps, preview, activePhase = '', completedSteps = [] }) {
  const selected = new Set(selectedSteps);
  const completed = new Set(completedSteps);
  const active = stageForStep(activePhase);
  return RUN_STAGES.map(stage => {
    const steps = stage.steps.filter(step => selected.has(step));
    const rows = steps.map(step => preview?.steps?.find(row => row.name === step));
    const known = steps.length > 0 && rows.every(Boolean);
    const requiredRows = rows.filter(row => !row?.disabled);
    const total = rows.reduce((sum, row) => sum + (Number(row?.total) || 0), 0);
    const pending = rows.reduce((sum, row) => sum + (Number(row?.will_run) || 0), 0);
    const skipped = rows.reduce((sum, row) => sum + (Number(row?.will_skip) || 0), 0);
    const warnings = rows.flatMap(row => row?.warnings || []);
    // A skip is not proof of a usable artifact: warnings and empty inputs stay visible.
    const reusable = known && requiredRows.every(row => Number(row?.total) > 0
      && Number(row?.will_run) === 0 && Number(row?.will_skip) === Number(row?.total)
      && !row?.warnings?.length);
    const done = steps.length > 0 && steps.every(step => completed.has(step));
    const status = !steps.length ? 'excluded' : active?.key === stage.key ? 'active'
      : done ? 'done' : !known ? 'unknown' : reusable ? 'reusable'
        : total === 0 || requiredRows.some(row => row?.warnings?.length) && pending === 0 ? 'blocked' : 'ready';
    return { ...stage, steps, status, total, pending, skipped, warnings };
  });
}

export function nextIncompleteStage(stages) {
  return stages.find(stage => !['excluded', 'done', 'reusable'].includes(stage.status)) || null;
}
