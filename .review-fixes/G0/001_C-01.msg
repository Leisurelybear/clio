test(i18n): read sources with utf-8-sig in the inventory guard

test_i18n_inventory reads sources with encoding="utf-8" and ast.parse(str)
rejects a leading U+FEFF, so any module saved with a BOM (plan_pacing.py was)
made both i18n guard tests error. Read with utf-8-sig so a BOM cannot break
the guard again. (The BOMs themselves were already removed in the tree.)

Refs: C-01
