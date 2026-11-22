fix(sidebar): serialize video removals to avoid lost updates

Removing a video does GET /api/videos/selected, filters client-side and
PUTs the whole list. Two quick removals overlapped, so the later PUT
restored the video removed by the first. Chain removals through a single
promise so each read-modify-write starts after the previous one finished.
Cross-tab conflicts still need server-side versioning (see note).

Refs: E-04
