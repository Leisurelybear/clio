fix(cut): restore stale cut artifacts after a failed or cancelled re-cut

Clips no longer in the plan are moved to .clio_bak before re-cutting, but
the finally block deleted those backups on success, failure and cancel
alike, so a failed or cancelled run lost the old output for good. Backups
are now deleted only after a completed run and moved back to their
original names otherwise.

Refs: B-08
