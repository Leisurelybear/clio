chore(i18n): drop unused analyze.window_slice_* locale keys

A-21 removed slice_window_video, the only user of the
analyze.window_slice_duration/_empty/_too_large messages. A repo-wide search
(Python, clio/ui/static, dynamic key building) finds no other reference, so
remove the three keys from en.json and zh-CN.json together.

Refs: A-21b
