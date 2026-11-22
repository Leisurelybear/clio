fix(rerun): cancel stale overlay hide timer on a new rerun

After a rerun finished, an untracked setTimeout hid the overlay 2-8s later.
Starting another rerun in that window let the old timer hide the new
overlay and unsubscribe from the new task, so its progress and final
refresh were lost. Track the hide timer, clear it in show/hide, and run
the post-success artifact refresh on its own timer. Regression test added.

Refs: E-06
