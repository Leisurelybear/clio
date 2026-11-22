fix(video): clamp Range end past EOF instead of returning 416

send_video_range answered 416 whenever last-byte-pos >= size, but
RFC 7233 §2.1 says it must be clamped to size-1. Players/downloaders that
probe with "bytes=0-<large>" or fixed chunk sizes failed to play.
Now end is clamped and only start >= size is unsatisfiable.
Adds a bytes=0-100 on 16 B test (206, bytes 0-15/16).

Refs: D-08
