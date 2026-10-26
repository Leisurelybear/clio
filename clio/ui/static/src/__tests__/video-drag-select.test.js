import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  DRAG_SELECT_THRESHOLD,
  initVideoDragSelect,
  normalizeDragRect,
  rectsIntersect,
  selectionFromDrag,
} from '../video-drag-select.js';

describe('normalizeDragRect', () => {
  it('normalizes a bottom-right drag', () => {
    expect(normalizeDragRect({ x: 10, y: 20 }, { x: 50, y: 80 })).toEqual({
      left: 10, top: 20, right: 50, bottom: 80, width: 40, height: 60,
    });
  });

  it('normalizes a top-left drag', () => {
    expect(normalizeDragRect({ x: 50, y: 80 }, { x: 10, y: 20 })).toEqual({
      left: 10, top: 20, right: 50, bottom: 80, width: 40, height: 60,
    });
  });

  it('tolerates missing points', () => {
    expect(normalizeDragRect(undefined, { x: 5, y: 6 })).toEqual({
      left: 0, top: 0, right: 5, bottom: 6, width: 5, height: 6,
    });
  });
});

describe('rectsIntersect', () => {
  const box = { left: 0, top: 0, right: 10, bottom: 10 };

  it('detects a partial overlap', () => {
    expect(rectsIntersect(box, { left: 5, top: 5, right: 20, bottom: 20 })).toBe(true);
  });

  it('rejects touching edges and disjoint boxes', () => {
    expect(rectsIntersect(box, { left: 10, top: 0, right: 20, bottom: 10 })).toBe(false);
    expect(rectsIntersect(box, { left: 30, top: 30, right: 40, bottom: 40 })).toBe(false);
    expect(rectsIntersect(box, null)).toBe(false);
  });
});

describe('selectionFromDrag', () => {
  const items = [
    { file: 'a.mp4', selectable: true, rect: { left: 0, top: 0, right: 100, bottom: 40 } },
    { file: 'b.mp4', selectable: true, rect: { left: 0, top: 40, right: 100, bottom: 80 } },
    { file: 'c.mp4', selectable: false, rect: { left: 0, top: 80, right: 100, bottom: 120 } },
  ];
  const rect = { left: 0, top: 0, right: 100, bottom: 100 };

  it('collects selectable hits in visual order', () => {
    expect(selectionFromDrag(items, rect)).toEqual(['a.mp4', 'b.mp4']);
  });

  it('keeps the existing selection when additive', () => {
    expect(selectionFromDrag(items, rect, { base: ['z.mp4', 'b.mp4'], additive: true }))
      .toEqual(['z.mp4', 'b.mp4', 'a.mp4']);
  });

  it('replaces the existing selection without additive', () => {
    expect(selectionFromDrag(items, rect, { base: ['z.mp4'] })).toEqual(['a.mp4', 'b.mp4']);
  });

  it('ignores missing, empty, and disabled items', () => {
    expect(selectionFromDrag(
      [...items, null, { file: '', selectable: true, rect }],
      rect,
    )).toEqual(['a.mp4', 'b.mp4']);
  });
});

describe('initVideoDragSelect', () => {
  let commit;
  let destroy;

  function rect(top) {
    return { left: 0, top, right: 200, bottom: top + 40, width: 200, height: 40 };
  }

  beforeEach(() => {
    document.body.innerHTML = `
      <div class="sidebar-scroll">
        <ul id="video-list">
          <li class="video-item"><input type="checkbox" class="video-checkbox" data-file="a.mp4"></li>
          <li class="video-item"><input type="checkbox" class="video-checkbox" data-file="b.mp4"></li>
          <li class="video-item missing"><input type="checkbox" class="video-checkbox" data-file="c.mp4" disabled></li>
        </ul>
      </div>
    `;
    const items = Array.from(document.querySelectorAll('.video-item'));
    items.forEach((li, index) => {
      li.getBoundingClientRect = () => rect(index * 40);
    });
    commit = vi.fn();
    let selection = [];
    destroy = initVideoDragSelect({
      enabled: () => true,
      selected: () => selection,
      commit: (files) => { selection = files; commit(files); },
    });
  });

  afterEach(() => {
    destroy?.destroy();
    destroy = null;
    vi.restoreAllMocks();
  });

  function dispatch(type, target, x, y) {
    target.dispatchEvent(new MouseEvent(type, {
      bubbles: true,
      cancelable: true,
      button: 0,
      clientX: x,
      clientY: y,
    }));
  }

  it('commits the rows covered by the drag rectangle', () => {
    const list = document.getElementById('video-list');
    dispatch('mousedown', list, 10, 5);
    dispatch('mousemove', window, 10, 75);
    dispatch('mouseup', window, 10, 75);

    expect(commit).toHaveBeenCalledWith(['a.mp4', 'b.mp4']);
    expect(document.querySelector('.video-drag-box')).toBeNull();
  });

  it('does not commit below the movement threshold', () => {
    const list = document.getElementById('video-list');
    dispatch('mousedown', list, 10, 5);
    dispatch('mousemove', window, 10, 5 + DRAG_SELECT_THRESHOLD - 1);
    dispatch('mouseup', window, 10, 5 + DRAG_SELECT_THRESHOLD - 1);

    expect(commit).not.toHaveBeenCalled();
  });

  it('cancels the drag with Escape', () => {
    const list = document.getElementById('video-list');
    dispatch('mousedown', list, 10, 5);
    dispatch('mousemove', window, 10, 75);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    dispatch('mouseup', window, 10, 75);

    expect(commit).not.toHaveBeenCalled();
    expect(document.querySelector('.video-drag-box')).toBeNull();
    expect(document.querySelector('[data-file="a.mp4"]').checked).toBe(false);
  });

  it('ignores mousedown on interactive controls', () => {
    const checkbox = document.querySelector('.video-checkbox');
    dispatch('mousedown', checkbox, 10, 5);
    dispatch('mousemove', window, 10, 75);
    dispatch('mouseup', window, 10, 75);

    expect(commit).not.toHaveBeenCalled();
  });
});
