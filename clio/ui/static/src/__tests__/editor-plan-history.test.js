import { describe, expect, it, vi } from 'vitest';

import {
  createPlanEditHistory,
  handlePlanHistoryShortcut,
} from '../editor-plan-history.js';

function makePlan(title = 'a') {
  return { day_title: 'day', sequence: [{ index: '001', title }] };
}

describe('plan edit history', () => {
  it('records a field edit once and restores it through undo/redo', () => {
    const controller = createPlanEditHistory();
    const plan = makePlan('a');
    controller.sync(plan);
    controller.beginFieldEdit(plan);
    plan.sequence[0].title = 'b';
    controller.commitFieldEdit(plan);

    const undone = controller.undo(plan);
    expect(undone.sequence[0].title).toBe('a');
    controller.adopt(undone);
    expect(controller.undo(undone)).toBeNull();

    const redone = controller.redo(undone);
    expect(redone.sequence[0].title).toBe('b');
  });

  it('drops history when the rendered plan object changes', () => {
    const controller = createPlanEditHistory();
    const first = makePlan('a');
    controller.record(first);
    first.sequence[0].title = 'b';

    const second = makePlan('c');
    controller.sync(second);

    expect(controller.undo(second)).toBeNull();
  });

  it('clears plan identity, snapshots, and both stacks on reset', () => {
    const controller = createPlanEditHistory();
    const plan = makePlan('a');
    controller.beginFieldEdit(plan);
    controller.record(plan);
    controller.reset();

    expect(controller.undo(plan)).toBeNull();
    expect(controller.commitFieldEdit(plan)).toBe(false);
  });
});

describe('plan history shortcuts', () => {
  it('runs undo and redo outside editable controls', () => {
    const undo = vi.fn(() => true);
    const redo = vi.fn(() => true);
    const undoEvent = new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, cancelable: true });
    const redoEvent = new KeyboardEvent('keydown', { key: 'y', metaKey: true, cancelable: true });

    expect(handlePlanHistoryShortcut(undoEvent, undo, redo)).toBe(true);
    expect(handlePlanHistoryShortcut(redoEvent, undo, redo)).toBe(true);
    expect(undoEvent.defaultPrevented).toBe(true);
    expect(redoEvent.defaultPrevented).toBe(true);
    expect(undo).toHaveBeenCalledOnce();
    expect(redo).toHaveBeenCalledOnce();
  });

  it('ignores editable targets and unhandled key combinations', () => {
    const undo = vi.fn(() => true);
    const redo = vi.fn(() => true);
    const input = document.createElement('input');
    const editableEvent = new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, cancelable: true });
    Object.defineProperty(editableEvent, 'target', { value: input });
    const altEvent = new KeyboardEvent('keydown', {
      key: 'z',
      ctrlKey: true,
      altKey: true,
      cancelable: true,
    });

    expect(handlePlanHistoryShortcut(editableEvent, undo, redo)).toBe(false);
    expect(handlePlanHistoryShortcut(altEvent, undo, redo)).toBe(false);
    expect(undo).not.toHaveBeenCalled();
    expect(redo).not.toHaveBeenCalled();
  });
});