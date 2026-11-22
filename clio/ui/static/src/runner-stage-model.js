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

function stepCountRows(stage, selected, previewRows, summary) {
  const sourceRows = Array.isArray(summary?.step_counts) ? summary.step_counts : null;
  const steps = stage.steps.filter(step => selected.has(step));
  const rows = sourceRows || steps
    .map(step => previewRows.find(row => row?.name === step))
    .filter(Boolean);
  return rows.map(row => ({
    name: String(row?.name || ''),
    total: Number(row?.total) || 0,
    willRun: Number(row?.will_run) || 0,
    willRunMax: Number(row?.will_run_max ?? row?.will_run) || 0,
    willSkip: Number(row?.will_skip) || 0,
    willSkipMin: Number(row?.will_skip_min ?? row?.will_skip) || 0,
    deferred: Boolean(row?.deferred),
    waitingOn: Array.isArray(row?.waiting_on) ? row.waiting_on.map(String) : [],
  }));
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
  const deferredRows = requiredRows.filter(row => row?.deferred);
  const total = rows.reduce((sum, row) => sum + (Number(row?.total) || 0), 0);
  const pending = rows.reduce((sum, row) => sum + (Number(row?.will_run) || 0), 0);
  const pendingMax = rows.reduce((sum, row) => sum + (Number(row?.will_run_max ?? row?.will_run) || 0), 0);
  const skipped = rows.reduce((sum, row) => sum + (Number(row?.will_skip) || 0), 0);
  const skippedMin = rows.reduce((sum, row) => sum + (Number(row?.will_skip_min ?? row?.will_skip) || 0), 0);
  const warnings = rows.flatMap(row => row?.warnings || []);
  // A skip is not proof of a usable artifact: warnings and empty inputs stay visible.
  const reusable = known && requiredRows.length > 0 && requiredRows.every(row => Number(row?.total) > 0
    && Number(row?.will_run) === 0 && Number(row?.will_skip) === Number(row?.total)
    && !row?.warnings?.length && !row?.cascade_assumed && !row?.deferred);
  const missingInput = requiredRows.some(row => Number(row?.total) === 0 && !row?.deferred);
  const blocked = known && !reusable && pending === 0 && (missingInput
    || (requiredRows.some(row => row?.warnings?.length) && deferredRows.length === 0));
  const waiting = known && !reusable && !blocked && pending === 0
    && deferredRows.length > 0 && deferredRows.length === requiredRows.length;
  const status = !steps.length ? 'excluded' : !known ? 'unknown' : requiredRows.length === 0 ? 'disabled'
    : reusable ? 'reusable' : blocked ? 'blocked' : waiting ? 'waiting' : 'ready';
  const waitingOn = [...new Set(deferredRows.flatMap(row => row?.waiting_on || []).map(String))].sort();
  const cascadeFrom = [...new Set(requiredRows.flatMap(row => row?.cascade_from || []).map(String))].sort();
  return {
    key: stage.key,
    steps,
    status,
    total,
    will_run: pending,
    will_run_max: pendingMax,
    will_skip: skipped,
    will_skip_min: skippedMin,
    warnings,
    blockers: status === 'blocked' || status === 'unknown' ? [...warnings] : [],
    estimate: sumUsage(rows),
    estimate_is_minimum: requiredRows.some(row => row?.estimate_is_minimum || row?.cascade_assumed || row?.deferred),
    deferred: waiting,
    waiting_on: waitingOn,
    cascade_from: cascadeFrom,
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
      stepCounts: stepCountRows(stage, selected, previewRows, summary),
      pending: Number(summary?.will_run) || 0,
      pendingMax: Number(summary?.will_run_max ?? summary?.will_run) || 0,
      skipped: Number(summary?.will_skip) || 0,
      skippedMin: Number(summary?.will_skip_min ?? summary?.will_skip) || 0,
      total: Number(summary?.total) || 0,
      warnings: summary?.warnings || [],
      blockers: summary?.blockers || [],
      estimateIsMinimum: Boolean(summary?.estimate_is_minimum),
      waitingOn: Array.isArray(summary?.waiting_on) ? summary.waiting_on : [],
      cascadeFrom: Array.isArray(summary?.cascade_from) ? summary.cascade_from : [],
    };
  });
}

export function nextIncompleteStage(stages, preview) {
  if (preview && Object.hasOwn(preview, 'next_stage')) {
    return stages.find(stage => stage.key === preview.next_stage) || null;
  }
  return stages.find(stage => !['excluded', 'disabled', 'done', 'reusable', 'waiting'].includes(stage.status)) || null;
}
