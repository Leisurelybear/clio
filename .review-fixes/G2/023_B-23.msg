fix(label): skip unreadable analysis JSON instead of aborting the batch

run_label_videos parsed every texts/*.json without a guard, so one corrupt
or half-written file (or a non-object root) raised and stopped labelling
for the whole project. Such files are now reported, marked as an error in
the processing state and skipped, as cut and transcript_align already do.

Refs: B-23
