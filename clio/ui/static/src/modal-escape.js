/**
 * Global Escape handling for `.modal` elements.
 *
 * Dynamically created modals register their own close routine as
 * `modal._closeModal` so Escape runs the same cleanup as their Cancel button
 * (clearing secret inputs, removing the node) instead of only hiding them.
 */

/** Close the topmost visible modal. Returns false when no modal is open. */
export function closeTopModal(root = document) {
  const openModals = [...root.querySelectorAll('.modal')].filter(m => m.style.display !== 'none');
  if (!openModals.length) return false;
  const top = openModals[openModals.length - 1];
  if (typeof top._closeModal === 'function') {
    top._closeModal();
  } else if (top.id === 'modal-relink') {
    import('./sidebar-relink.js').then(m => m.closeRelinkModal());
  } else if (top.id === 'modal-video-manage') {
    import('./sidebar-video-manage.js').then(m => m.closeVideoManager());
  } else if (top.id === 'modal-batch-relink') {
    import('./sidebar-batch-relink.js').then(m => m.closeBatchRelinkModal());
  } else {
    top.style.display = 'none';
  }
  return true;
}
