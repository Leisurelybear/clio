fix(video-info): dedupe probes and close cards of replaced rows

_loadInfo cached only finished results, so hover plus modal (or repeated
hovers) started several concurrent ffprobe requests. A hover timer firing
after renderVideoList() replaced the row showed the card at 0,0, and it
never closed because the old row gets no mouseleave. Share in-flight
promises, cap the cache at 200 entries, skip/close cards for disconnected
rows, and close the card at the start of renderVideoList(). Tests added.

Refs: E-16
