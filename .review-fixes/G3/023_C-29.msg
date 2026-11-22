fix(config): raise ValueError for unsupported export.output_subdir

_validate_config raised NotImplementedError for export.output_subdir values
other than "export", while every other field raises ValueError. Callers that
turn ValueError into a readable message treated it as an unknown error
(HTTP 500 / traceback). It is now a ValueError with the same message.

Refs: C-29
