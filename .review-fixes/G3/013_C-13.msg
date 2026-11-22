fix(ai): let KeyboardInterrupt escape the Gemini retry wrapper

_call_with_retry caught BaseException and wrapped KeyboardInterrupt,
SystemExit and GeneratorExit as RuntimeError("gemini_non_retryable"), so a
Ctrl+C during a CLI run was treated as one failed clip and processing went
on. The wrapper now catches Exception only.

Refs: C-13
