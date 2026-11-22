fix(ai): report non-JSON replies and retry transient 4xx statuses

A 200 reply that was not JSON (proxy login page, gateway HTML) raised a bare
JSONDecodeError with no URL or status, and a JSON list raised AttributeError.
408/409/425 failed at once as permanent 4xx errors. Both bad bodies now raise
the usual api_error_response ValueError with URL, status and a body excerpt,
and 408/409/425 join 429 and 5xx in the retried set.

Refs: C-12
