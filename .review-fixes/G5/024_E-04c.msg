fix(sidebar): remove videos via atomic server endpoint

Deleting videos still did GET /api/videos/selected followed by a PUT of the
whole filtered list, so a clip added from another tab or the video manager in
between was silently dropped. The client now only resolves its targets to the
stored entries and posts them to POST /api/videos/selected/remove, which
filters videos.json under the server lock. The per-tab serialization stays.

Refs: E-04c
