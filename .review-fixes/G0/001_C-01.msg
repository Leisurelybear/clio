fix(encoding): strip UTF-8 BOM from plan_pacing sources

clio/plan_pacing.py and its test started with U+FEFF. Imports were fine, but
test_i18n_inventory reads sources with encoding="utf-8" and ast.parse(str)
rejects the BOM, so both i18n guard tests errored on every CI run.

- remove the BOM from both files
- read sources with utf-8-sig in the inventory test so a future BOM cannot
  break the guard again

Also covers A-05 / B-25.

Refs: C-01
