fix(asr): treat every non-running DashScope status as terminal

_poll_task only recognised SUCCEEDED and FAILED, so UNKNOWN (expired or
missing task), CANCELED and other terminal states were polled until the
30-minute timeout. A response without "output" raised a bare KeyError.
Any status other than PENDING/RUNNING now fails with the provider message
(or the status), and missing fields raise a localized error.

Refs: B-18
