fix(tasks): escape formula-like cells in summary.csv

summary.csv is written as utf-8-sig for Excel, but LLM-written fields
(title, summary, location, ...) were written verbatim. A value starting
with = + - @ or a tab/CR, which speech or captions in a video can induce,
was evaluated as a formula when the file was opened. Such cells now get a
leading apostrophe, which is stripped again when summary.csv is merged.

Refs: B-19
