fix(ui): run modal cleanup when closing dialogs with Escape

The global Escape handler only set display:none on the topmost .modal.
The credential-replace and provider dialogs are created on the fly and
have no own Escape handling, so they stayed in the DOM with the typed API
key in their password inputs and piled up. Move the logic to
modal-escape.js and let dynamic modals register _closeModal, which clears
secret inputs and removes the node. Tests added.

Refs: E-10
