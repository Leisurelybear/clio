fix(viewer): drop stale loadedmetadata handler on source switch

Each source switch added a one-shot loadedmetadata listener. Switching
again before the first source's metadata arrived left that listener
attached, so it fired together with the new one and seeked/played with
the old seekSec/wantPlay (extra Range requests, a flash of playback), and
listeners piled up when loads failed. Track the pending handler and remove
it before the next switch. Regression test added.

Refs: E-15
