fix(cli): sanitize export day dir and catch unprepared command errors

The default export directory joined the raw --day value, so --day "../x"
wrote outside output_dir/export (reads already went through plan_day_stem).
transcribe/whisper/compare-models/check ran outside the CLI error boundary
and printed a traceback instead of cli.error + exit 1. Use plan_day_stem for
the export dir and wrap _run_unprepared_command in the same boundary.

Refs: A-24
