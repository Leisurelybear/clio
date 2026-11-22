fix(waveform,transcribe): bound ffprobe/ffmpeg runs and honor cancel

has_audio_stream ran ffprobe without a timeout and the waveform decode
had neither timeout nor cancel, so a stalled network drive could pin both
waveform slots forever. ffprobe now times out after 30 s, the decode stops
after 600 s (below the lock's STALE_SEC) and waveform tasks are cancellable.
Audio extraction for transcription now also stops ffmpeg on cancel while it
prints nothing, and escalates terminate() to kill() after 5 s.

Refs: B-14
