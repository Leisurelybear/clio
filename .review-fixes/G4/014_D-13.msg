fix(videos): only case-fold video path keys on Windows

_video_key lowercased the resolved path on every platform, so on Linux or
a case-sensitive macOS volume A.mp4 and a.mp4 were treated as the same
file: PUT /api/videos/selected dropped one and relink reported a false
409 "already in project". It now uses os.path.normcase, which folds case
and separators only on Windows. Adds a platform-aware test.

Refs: D-13
