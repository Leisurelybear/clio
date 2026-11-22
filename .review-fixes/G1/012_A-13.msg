refactor(timecode): share one timestamp parser across analysis and plans

Five modules parsed timeline timestamps with different rules: readiness
and evaluation accepted plain-second strings like "12.5", analyze,
analyze_windows and plan_input did not. So shift_analysis_times left such
values in window-relative time and plan range validation skipped clips whose
intervals could not be parsed. Add clio/timecode.py with parse_timestamp
(lenient, for model output) and parse_timestamp_strict (validation) and use
them everywhere; plain-second strings keep their string form when shifted.

Refs: A-13
