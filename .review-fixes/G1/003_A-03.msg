fix(readiness): tolerate ffprobe failures when collecting durations

run_probe raises subprocess.CalledProcessError when ffprobe exits non-zero,
which is neither OSError nor RuntimeError. One corrupt or half-written mp4
without .vmeta in compressed/ therefore made the cut/export readiness check
raise. Also catch subprocess.SubprocessError and treat the duration as
unknown for that clip.

Refs: A-03
