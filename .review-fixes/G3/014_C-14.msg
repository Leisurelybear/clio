fix(ai): stop retrying local file errors in the Gemini provider

_is_retryable treated every OSError as transient, including
FileNotFoundError and PermissionError, and analyze_images read the images
inside the retry closure, so a missing file waited through 2s, 4s ... of
backoff before failing. Local file errors are now non-retryable (network
OSErrors still retry) and images are read once before the retry loop.

Refs: C-14
