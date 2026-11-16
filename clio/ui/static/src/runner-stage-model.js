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

/** Runtime uses voiceover for both script tasks; disambiguate using task context. */
export function resolveRunPhase(phase, steps = [], previousPhase = '') {
  if (phase === 'voiceover' && steps.includes('plan_voiceover')
    && (!steps.includes('voiceover') || previousPhase === 'plan_voiceover')) {
    return 'plan_voiceover';
  }
  return phase || '';
}

function sumUsage(rows) {
  const calls = rows.reduce((sum, row) => sum + (Number(row?.planned_calls) || 0), 0);
  const priced = rows.some(row => Number(row?.planned_calls) > 0)
    ? rows.filter(row => Number(row?.planned_calls) > 0).every(row => row?.estimate_status === 'estimated')
    : false;
  return {
    planned_calls: calls,
    estimated_cost_usd: priced
      ? rows.reduce((sum, row) => sum + (Number(row?.estimated_cost_usd) || 0), 0) : null,
    estimate_status: !calls ? 'not_applicable' : priced ? 'estimated' : 'unknown',
  };
}

/**
 * Fallback stage summary for responses that predate the authoritative
 * `preview.stages` contract (older backends, cached payloads, partial mocks).
 * The backend projection always wins when it is present.
 */
function deriveStageSummary(stage, selected, previewRows) {
  const steps = stage.steps.filter(step => selected.has(step));
  const rows = steps.map(step => previewRows.find(row => row?.name === step));
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
  const status = !steps.length ? 'excluded' : !known ? 'unknown' : reusable ? 'reusable'
    : total === 0 || (requiredRows.some(row => row?.warnings?.length) && pending === 0) ? 'blocked' : 'ready';
  return {
    key: stage.key,
    steps,
    status,
    total,
    will_run: pending,
    will_skip: skipped,
    warnings,
    blockers: status === 'blocked' || status === 'unknown' ? [...warnings] : [],
    estimate: sumUsage(rows),
    output: null,
  };
}

export function buildRunStageModel({ selectedSteps, preview, activePhase = '', completedSteps = [] }) {
  const selected = new Set(selectedSteps);
  const completed = new Set(completedSteps);
  const active = stageForStep(activePhase);
  const previewRows = Array.isArray(preview?.steps) ? preview.steps : [];
  return RUN_STAGES.map(stage => {
    const summary = preview?.stages?.find(item => item.key === stage.key)
      || deriveStageSummary(stage, selected, previewRows);
    const steps = summary?.steps || stage.steps.filter(step => selected.has(step));
    const done = steps.length > 0 && steps.every(step => completed.has(step));
    const status = active?.key === stage.key ? 'active' : done ? 'done' : summary?.status || 'unknown';
    return {
      ...stage,
      ...summary,
      steps,
      status,
      pending: Number(summary?.will_run) || 0,
      skipped: Number(summary?.will_skip) || 0,
      total: Number(summary?.total) || 0,
      warnings: summary?.warnings || [],
    };
  });
}

export function nextIncompleteStage(stages, preview) {
  if (preview && Object.hasOwn(preview, 'next_stage')) {
    return stages.find(stage => stage.key === preview.next_stage) || null;
  }
  return stages.find(stage => !['excluded', 'done', 'reusable'].includes(stage.status)) || null;
}
