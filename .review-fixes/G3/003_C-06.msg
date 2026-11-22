fix(config): read .env files with a UTF-8 BOM

A .env saved by Windows Notepad starts with U+FEFF. Read as utf-8 the first
line became "\ufeffGEMINI_API_KEY=...", the key regex did not match and the
first key was silently dropped. The loader and DotenvStore now read .env with
utf-8-sig and parse_dotenv strips a leading BOM; rewrites no longer carry it.
(The /api/env route is fixed separately.)

Refs: C-06
