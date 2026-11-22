fix(waveform): stop re-running ffmpeg after a decode failure

A failed ffmpeg decode returned a probe_error payload that was written as a
ready cache file and cleared the .error cool-down, while read_peaks treats
probe_error as "no cache". Every /waveform poll on a broken file therefore
spawned a new ffmpeg and TaskCenter task. Both the managed task and the daemon
job now raise on probe_error so the .error cool-down is recorded instead.

Refs: B-01
