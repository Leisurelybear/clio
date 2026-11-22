refactor: share cut/export plan checks and atomic state writes

_run_cut_command and _run_export_command duplicated ~25 lines of plan
loading and readiness checking; move them into _load_checked_plan.
ProgressTracker and ProcessingState each hand-rolled "random tmp +
replace" without fsync or path_write_lock, and ProcessingState left a .tmp
behind when the write failed. Both now use utils.write_text_atomic (output
stays compact JSON).

Refs: A-23
