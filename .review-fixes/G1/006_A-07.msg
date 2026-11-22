fix(utils): make run_probe cancellable and stop truncating JSON

run_probe checked cancel_event only once before communicate(), so a cancel
could block for up to 30s, and the cancel path terminated without wait(),
leaving a zombie. max_output_bytes also cut JSON output mid-document, so
probe_media_details / creation-time probes on GoPro or multi-track files
failed to parse. Poll communicate() in short slices while a cancel_event is
given, reap the process on cancel, and never truncate -of json output.

Refs: A-07
