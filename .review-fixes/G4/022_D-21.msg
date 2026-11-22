fix(plan): strip only the trailing _plan suffix from day labels

GET /api/plans derived day_label with stem.replace("_plan", ""), which
removed every occurrence: trip_plan_b_plan.json became "trip_b", and the
following GET /api/plan?day=trip_b returned 404.
It now uses str.removesuffix("_plan"). Adds a test.

Refs: D-21
