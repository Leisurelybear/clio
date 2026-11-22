fix(readiness): keep collecting vmeta durations without ffprobe

When ffprobe could not be resolved, collect_project_media_durations hit
`break` and stopped scanning, so later clips that carry a .vmeta duration
were dropped too. Missing durations silently disabled the error-level
timeline_out_of_bounds check. Mark ffprobe as unavailable and skip only
the clips that actually need probing.

Refs: A-04
