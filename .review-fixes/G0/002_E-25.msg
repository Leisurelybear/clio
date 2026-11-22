chore(ui): strip UTF-8 BOM from frontend modules

Browsers and vitest strip the BOM, so there is no runtime change; this only
removes diff noise and keeps tooling that inspects the first line honest.

Refs: E-25
