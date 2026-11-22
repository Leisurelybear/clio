fix(toast): remove toasts when animationend never fires

removeToast relied solely on animationend to detach the node and dequeue
the next toast. With prefers-reduced-motion the removing animation is
`none`, so the event never fired: hidden toasts kept the 3 visible slots
and every later toast stayed queued forever. Finalize once on either
animationend or a 250ms fallback timer; add a reduced-motion regression test.

Refs: E-01
