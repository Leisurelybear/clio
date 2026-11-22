fix(transcripts): validate initial segments on transcript create

POST /api/transcripts {create: true, segments} only float()-ed start/end,
so "nan"/"inf", negative or reversed times and unbounded text were saved;
json.dumps then wrote NaN/Infinity, which is not JSON, and the transcript
could no longer be opened in the UI. Create now applies the same finite,
>= 0, end > start, <= 24 h and text-length rules as append (shared helper)
and serializes with allow_nan=False. Adds parametrized tests.

Refs: D-20
