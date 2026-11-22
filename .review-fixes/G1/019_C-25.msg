fix(ffmpeg): pick the current release asset from the checksum list

Downloads use the mutable releases/download/latest, but the asset names
were hard-coded as ffmpeg-n9.0-latest-*, so the installer would 404 as soon
as upstream moved to n9.1. Resolve the archive name from checksums.sha256:
keep the known name when it is listed, otherwise take the newest
ffmpeg-nX.Y-latest-<platform>-gpl build of the same platform and type.

Refs: C-25
