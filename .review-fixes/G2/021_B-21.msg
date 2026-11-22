fix(plan): stop the trip summary from overwriting a day plan

run_plan_all_days wrote its summary to trip_plan.json, the same name a day
labelled "trip" gets for its own plan, so that day's plan was overwritten;
it also showed up as a fake "trip" day in *_plan.json listings. Labels such
as "day 1" and "day_1" sanitize to the same file and overwrote each other.
The summary is now _trip_summary.json and clashing labels are rejected
before any plan is generated.

Refs: B-21
