fix(asr): honor Retry-After instead of capping it at 3 seconds

request_with_retry clamped a server Retry-After to max_delay (3 s), so a
429 with Retry-After: 30 was retried three times inside the rate-limit
window and failed anyway. Retry-After is now a lower bound for the wait;
values above max_retry_after (60 s) fail immediately with the usual
HTTP error message. max_delay still caps the exponential backoff.

Refs: B-16
