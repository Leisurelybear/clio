fix(io): accept UTF-8 BOM when reading plan and analysis JSON

Plans, analysis texts and evaluation manifests were read with
encoding="utf-8", so a file saved by Windows Notepad (with BOM) failed with
JSONDecodeError on CLI cut/export, and run_scope silently treated such an
analysis as {} and dropped it from the run scope. Read these files with
utf-8-sig (identical for BOM-less files) in main, run_scope, evaluation and
plan_readiness.

Refs: A-06
