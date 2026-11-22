fix(analyze): honour cancel in planning and unify cancel exception

plan_daily_vlog.call_stage did not pass cancel_event to _call_ai, so the
pre-call cancel check never ran for the 5-7 planning AI calls. Cancellation
also raised RuntimeError in _call_ai/analyze_video/voiceover but
InterruptedError in refine/summarize, so callers could report a user cancel
as a failure. Forward cancel_event and raise one AnalysisCancelled
(InterruptedError + RuntimeError, so existing handlers keep working).

Refs: A-11
