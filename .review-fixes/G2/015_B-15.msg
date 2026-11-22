perf(waveform): compute peaks with array slices instead of a sample loop

peaks_from_pcm_s16le unpacked every sample in a Python loop: an hour of
8 kHz audio is ~29M iterations holding the GIL for tens of seconds and
stalling HTTP/SSE threads. Samples are now decoded once into array('h')
and each bin is reduced with C-level max/min; the bin assignment is
unchanged (checked against the old loop in a test). About 8x faster.

Refs: B-15
