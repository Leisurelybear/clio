fix(utils): keep Windows ffmpeg search roots off POSIX and the CWD

_ffmpeg_search_roots ran on every platform. With LOCALAPPDATA/USERPROFILE
unset, Path("") is the CWD, and "C:/Program Files/..." is a relative path on
POSIX, so discovery globbed the working directory for bin/ffmpeg - a planted
binary could be picked up. Only add the Windows roots on nt, skip roots
whose environment variable is empty, and keep FFMPEG_HOME everywhere.

Refs: A-20
