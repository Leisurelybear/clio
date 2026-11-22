fix(ai): cap provider timeouts for interactive AI requests

/api/ai/models and /api/ai/test run on the short-request budget (32
threads) but used the provider timeout as-is, including a timeout_sec
submitted with an unsaved draft, so a few slow or huge-timeout calls
could exhaust the pool and make the whole UI return 503.
Model listing now uses at most 30 s; draft timeouts for the connection
test are clamped to the 120 s default (non-finite values included).

Refs: D-18
