fix(fs): skip unreadable entries in /api/fs/videos

handle_get_fs_videos called entry.stat() without a per-entry guard, so a
single broken symlink, cloud placeholder or unreadable file raised OSError
out of the loop and the whole directory listing returned 500.
Each entry is now wrapped in try/except OSError and skipped, matching
/api/fs/entries. Adds a test with a dangling entry.

Refs: D-19
