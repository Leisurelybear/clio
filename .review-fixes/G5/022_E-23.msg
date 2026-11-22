perf(credentials): share one removal observer across fields

Every credential field created its own MutationObserver on the whole
document.body subtree, so any DOM change anywhere (log appends, list
re-renders, toasts) ran N callbacks. Use a single shared observer that
ignores batches without removed nodes, disposes detached fields (clearing
revealed values and the visibilitychange listener) and disconnects when
no field is left. Test added.

Refs: E-23
