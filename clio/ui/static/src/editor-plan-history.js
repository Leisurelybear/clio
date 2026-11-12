// @ts-check
/** Stateful undo/redo wiring for plan editing. */

import {
  createPlanHistory,
  planSnapshot,
  pushPlanHistory,
  redoPlanSnapshot,
  resetPlanHistory,
  undoPlanSnapshot,
} from './plan-edit.js';

/**
 * Keep the history stack aligned with the currently rendered plan object.
 * Field snapshots capture the pre-edit state once per focus session.
 */
export function createPlanEditHistory() {
  const history = createPlanHistory();
  let planRef = null;
  let fieldEditSnapshot = null;

  /** @param {any} plan */
  function sync(plan) {
    if (planRef === plan) return false;
    resetPlanHistory(history);
    planRef = plan;
    fieldEditSnapshot = null;
    return true;
  }

  return {
    /** @param {any} plan */
    sync,
    reset() {
      resetPlanHistory(history);
      planRef = null;
      fieldEditSnapshot = null;
    },
    /** @param {any} restoredPlan */
    adopt(restoredPlan) {
      planRef = restoredPlan;
      fieldEditSnapshot = null;
    },
    /** @param {any} plan */
    record(plan) {
      sync(plan);
      return pushPlanHistory(history, planSnapshot(plan));
    },
    /** @param {any} plan */
    beginFieldEdit(plan) {
      sync(plan);
      fieldEditSnapshot = planSnapshot(plan);
    },
    /** @param {any} plan */
    commitFieldEdit(plan) {
      if (fieldEditSnapshot == null) return false;
      const snapshot = fieldEditSnapshot;
      fieldEditSnapshot = null;
      return pushPlanHistory(history, snapshot);
    },
    /** @param {any} plan */
    undo(plan) {
      sync(plan);
      return undoPlanSnapshot(history, planSnapshot(plan));
    },
    /** @param {any} plan */
    redo(plan) {
      sync(plan);
      return redoPlanSnapshot(history, planSnapshot(plan));
    },
  };
}

/**
 * Apply Ctrl/Cmd+Z and Ctrl/Cmd+Shift+Z (or Ctrl/Cmd+Y) when focus is outside
 * editable controls. Returns true when a history action handled the event.
 * @param {KeyboardEvent} event
 * @param {() => boolean} undo
 * @param {() => boolean} redo
 */
export function handlePlanHistoryShortcut(event, undo, redo) {
  if (!(event.ctrlKey || event.metaKey) || event.altKey) return false;
  const target = event.target;
  if (target instanceof HTMLElement
    && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) {
    return false;
  }
  if (event.key === 'z' && !event.shiftKey) {
    if (!undo()) return false;
    event.preventDefault();
    return true;
  }
  if (event.key === 'y' || (event.key === 'z' && event.shiftKey)) {
    if (!redo()) return false;
    event.preventDefault();
    return true;
  }
  return false;
}