/**
 * Rubber-band selection for the sidebar video list.
 *
 * The pure helpers (rect math / selection merge) are exported separately so
 * they can be tested without a DOM layout. `initVideoDragSelect()` binds the
 * pointer behavior once to `#video-list`; the list element itself survives
 * every `renderVideoList()` because only its children are replaced.
 */

export const DRAG_SELECT_THRESHOLD = 4;
const EDGE_SCROLL_PX = 28;
const EDGE_SCROLL_STEP = 14;

/** Build a top-left/bottom-right rectangle from two drag points. */
export function normalizeDragRect(start, end) {
  const a = start || {};
  const b = end || {};
  const left = Math.min(Number(a.x) || 0, Number(b.x) || 0);
  const top = Math.min(Number(a.y) || 0, Number(b.y) || 0);
  const right = Math.max(Number(a.x) || 0, Number(b.x) || 0);
  const bottom = Math.max(Number(a.y) || 0, Number(b.y) || 0);
  return { left, top, right, bottom, width: right - left, height: bottom - top };
}

/** True when two viewport rectangles overlap with a non-empty area. */
export function rectsIntersect(a, b) {
  if (!a || !b) return false;
  return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
}

/**
 * Resolve the selection produced by one drag rectangle.
 * `items` is `[{ file, rect, selectable }]`, in visual order.
 * Without `additive`, the previous selection is replaced; with it, the
 * previous selection is kept and new hits are appended (Ctrl/Cmd drag).
 */
export function selectionFromDrag(items, rect, { base = [], additive = false } = {}) {
  const out = [];
  const seen = new Set();
  if (additive) {
    for (const file of Array.isArray(base) ? base : []) {
      if (!file || seen.has(file)) continue;
      seen.add(file);
      out.push(file);
    }
  }
  for (const item of Array.isArray(items) ? items : []) {
    if (!item || !item.file || !item.selectable) continue;
    if (!rectsIntersect(item.rect, rect)) continue;
    if (seen.has(item.file)) continue;
    seen.add(item.file);
    out.push(item.file);
  }
  return out;
}

function _scrollParent(el) {
  let node = el?.parentElement || null;
  while (node && node !== document.body) {
    const style = getComputedStyle(node);
    if (/(auto|scroll)/.test(style.overflowY || '') && node.scrollHeight > node.clientHeight) return node;
    node = node.parentElement;
  }
  return null;
}

function _isInteractive(target) {
  if (!target?.closest) return false;
  return !!target.closest(
    'input, button, a, select, textarea, [contenteditable], '
    + '.selection-header, .video-group-header, .video-actions, .match-jump',
  );
}

/**
 * Bind drag selection to the video list.
 *
 * @param {object} options
 * @param {() => boolean} options.enabled selection mode predicate
 * @param {() => string[]} options.selected current selection snapshot
 * @param {(files: string[]) => void} options.commit applied after a completed drag
 * @param {HTMLElement} [options.list] defaults to `#video-list`
 * @returns {{ destroy: () => void } | null}
 */
export function initVideoDragSelect({ enabled, selected, commit, list } = {}) {
  const el = list || document.getElementById('video-list');
  if (!el || el.dataset.dragSelectBound === '1') return null;
  el.dataset.dragSelectBound = '1';

  let drag = null;

  const collectItems = () => Array.from(el.querySelectorAll('.video-item')).map((li) => {
    const cb = li.querySelector('.video-checkbox');
    return {
      el: li,
      file: cb?.dataset?.file || '',
      selectable: !!cb && !cb.disabled && !li.classList.contains('missing'),
      rect: li.getBoundingClientRect(),
    };
  });

  const createBox = () => {
    const box = document.createElement('div');
    box.className = 'video-drag-box';
    box.dataset.dragBox = '1';
    document.body.appendChild(box);
    return box;
  };

  const updateBox = () => {
    if (!drag?.box) return;
    const rect = normalizeDragRect(drag.start, drag.current);
    Object.assign(drag.box.style, {
      left: `${rect.left}px`,
      top: `${rect.top}px`,
      width: `${rect.width}px`,
      height: `${rect.height}px`,
    });
  };

  const previewHits = () => {
    if (!drag?.moved) return [];
    const rect = normalizeDragRect(drag.start, drag.current);
    const files = selectionFromDrag(drag.items, rect, {
      base: drag.base,
      additive: drag.additive,
    });
    const hit = new Set(files);
    for (const item of drag.items) {
      if (!item.file) continue;
      item.el.classList.toggle('drag-hit', hit.has(item.file));
      const cb = item.el.querySelector('.video-checkbox');
      if (cb) cb.checked = hit.has(item.file);
    }
    drag.pending = files;
    return files;
  };

  const clearPreview = () => {
    if (!drag) return;
    const original = new Set(drag.original || []);
    for (const item of drag.items || []) {
      item.el.classList.remove('drag-hit');
      const cb = item.el.querySelector('.video-checkbox');
      if (cb) cb.checked = original.has(item.file);
    }
    drag.box?.remove();
  };

  const stopAutoScroll = () => {
    if (drag?.raf) cancelAnimationFrame(drag.raf);
    if (drag) drag.raf = 0;
  };

  const autoScrollTick = () => {
    if (!drag?.moved || !drag.scroller) return;
    const bounds = drag.scroller.getBoundingClientRect();
    let delta = 0;
    if (drag.current.y < bounds.top + EDGE_SCROLL_PX) {
      delta = -Math.ceil(((bounds.top + EDGE_SCROLL_PX) - drag.current.y) / EDGE_SCROLL_PX * EDGE_SCROLL_STEP);
    } else if (drag.current.y > bounds.bottom - EDGE_SCROLL_PX) {
      delta = Math.ceil((drag.current.y - (bounds.bottom - EDGE_SCROLL_PX)) / EDGE_SCROLL_PX * EDGE_SCROLL_STEP);
    }
    if (delta) {
      drag.scroller.scrollTop += delta;
      previewHits();
    }
    drag.raf = requestAnimationFrame(autoScrollTick);
  };

  const unbindWindow = () => {
    window.removeEventListener('mousemove', onMouseMove);
    window.removeEventListener('mouseup', onMouseUp);
    document.removeEventListener('keydown', onKeyDown);
    stopAutoScroll();
    document.body.classList.remove('video-drag-active');
  };

  const suppressNextClick = () => {
    const handler = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      document.removeEventListener('click', handler, true);
    };
    document.addEventListener('click', handler, true);
    setTimeout(() => document.removeEventListener('click', handler, true), 0);
  };

  const endDrag = () => {
    const finished = drag;
    clearPreview();
    unbindWindow();
    drag = null;
    return finished;
  };

  const finishDrag = () => {
    const finished = endDrag();
    if (!finished?.moved) return;
    suppressNextClick();
    commit?.(finished.pending || []);
  };

  const cancelDrag = () => {
    const cancelled = endDrag();
    if (cancelled?.moved) suppressNextClick();
  };

  function onMouseDown(e) {
    if (!enabled?.() || e.button !== 0) return;
    if (_isInteractive(e.target)) return;
    const original = (selected?.() || []).slice();
    drag = {
      start: { x: e.clientX, y: e.clientY },
      current: { x: e.clientX, y: e.clientY },
      original,
      base: (e.ctrlKey || e.metaKey) ? original : [],
      additive: !!(e.ctrlKey || e.metaKey),
      items: collectItems(),
      scroller: _scrollParent(el),
      moved: false,
      box: null,
      pending: null,
      raf: 0,
    };
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    document.addEventListener('keydown', onKeyDown);
  }

  function onMouseMove(e) {
    if (!drag) return;
    if (!enabled?.()) { cancelDrag(); return; }
    drag.current = { x: e.clientX, y: e.clientY };
    if (!drag.moved) {
      const dx = Math.abs(e.clientX - drag.start.x);
      const dy = Math.abs(e.clientY - drag.start.y);
      if (Math.max(dx, dy) < DRAG_SELECT_THRESHOLD) return;
      drag.moved = true;
      document.body.classList.add('video-drag-active');
      document.getSelection?.()?.removeAllRanges?.();
      if (!drag.box) drag.box = createBox();
      if (drag.scroller) {
        cancelAnimationFrame(drag.raf);
        drag.raf = requestAnimationFrame(autoScrollTick);
      }
    }
    e.preventDefault();
    updateBox();
    previewHits();
  }

  function onMouseUp(e) {
    if (!drag) return;
    if (drag.moved) {
      drag.current = { x: e.clientX, y: e.clientY };
      updateBox();
      previewHits();
    }
    finishDrag();
  }

  function onKeyDown(e) {
    if (e.key !== 'Escape' || !drag) return;
    e.preventDefault();
    cancelDrag();
  }

  const onDragStart = (e) => {
    if (enabled?.()) e.preventDefault();
  };

  el.addEventListener('mousedown', onMouseDown);
  el.addEventListener('dragstart', onDragStart);

  return {
    destroy() {
      unbindWindow();
      clearPreview();
      drag = null;
      el.removeEventListener('mousedown', onMouseDown);
      el.removeEventListener('dragstart', onDragStart);
      delete el.dataset.dragSelectBound;
    },
  };
}
