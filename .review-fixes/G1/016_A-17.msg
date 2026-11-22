fix(evaluation): match facts only against model-written content

_score_facts searched every string in the prediction JSON, including
source_file, compressed_file, media_identity and _lineage. A fact alias
that only appeared in a file name (e.g. 海边 in 海边日落.mp4) counted as
described, inflating the facts metric (weight 0.25). Skip private keys,
id/index/media_identity/scope_id and *_file/*_path/*_stem values.

Refs: A-17
