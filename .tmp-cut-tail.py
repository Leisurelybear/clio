def _cut_source_stem(video_path: Path, source: str) -> str:
    """Return the original source stem used by processing-state bookkeeping."""
    stem = video_path.stem
    if source == "original":
        return stem
    if "_" in stem:
        stem = stem.split("_", 1)[1]
    return SEGMENT_SUFFIX_RE.sub("", stem)


def _selected_cut_media(
    path: Path,
    project_selection: set[str] | None,
    *,
    artifact: bool = False,
) -> bool:
    return project_selection is None or _matches_selected_stem(path, project_selection, artifact=artifact)


def _resolve_cut_video_path(
    config: AppConfig,
    comp_dir: Path,
    idx: str,
    source: str,
    project_selection: set[str] | None,
) -> Path | None:
    if source == "compressed":
        candidates = [
            path
            for path in _indexed_files(comp_dir, idx, index_width=config.naming.index_width)
            if path.suffix.lower() in VIDEO_EXTS and _selected_cut_media(path, project_selection, artifact=True)
        ]
        return candidates[0] if candidates else None

    comp_candidates = [
        path
        for path in _indexed_files(comp_dir, idx, index_width=config.naming.index_width)
        if path.suffix.lower() in VIDEO_EXTS and _selected_cut_media(path, project_selection, artifact=True)
    ]
    if not comp_candidates:
        return None
    compressed = comp_candidates[0]

    # Prefer .vmeta: it directly identifies the original path (O(1), any depth).
    meta = VideoMeta.read(compressed)
    if meta is not None:
        src = meta.source_path_obj()
        if src.is_file():
            return src if _selected_cut_media(src, project_selection) else None

    # Fallback: reverse the compressed name and consult videos.json.
    suffix = compressed.stem.split("_", 1)[1].lower()
    from clio.tasks._video_loader import source_videos

    videos = source_videos(config)

    def _unique_stem_hit(needle: str) -> Path | None:
        hits = [p for p in videos if p.stem.lower() == needle]
        return hits[0] if len(hits) == 1 else None

    hit = _unique_stem_hit(suffix)
    if hit is not None:
        return hit
    orig_stem = SEGMENT_SUFFIX_RE.sub("", suffix)
    if orig_stem != suffix:
        return _unique_stem_hit(orig_stem)
    return None


def _record_cut_skip(
    *,
    video_path: Path | None,
    source: str,
    state: ProcessingState,
    tracker: Any | None,
    message: str,
    tracker_message: str,
) -> None:
    print(message)
    if video_path is not None:
        state.mark(_cut_source_stem(video_path, source), "cut", "skipped")
    if tracker is not None:
        tracker.next(message=tracker_message)


def _prepare_cut_range(
    *,
    video_path: Path,
    timeline: str,
    title: str,
    ffprobe: str,
    segment_number: int,
    source: str,
    state: ProcessingState,
    tracker: Any | None,
) -> tuple[float, float, float, float] | None:
    try:
        start, end = parse_time_range(timeline)
    except ValueError as e:
        _record_cut_skip(
            video_path=video_path,
            source=source,
            state=state,
            tracker=tracker,
            message=t("task.cut.skip_bad_time", {"timeline": timeline, "detail": e}),
            tracker_message=t("task.cut.skip_bad_time_tracker", {"index": segment_number}),
        )
        return None

    plan_start, plan_end = start, end
    if end <= start:
        _record_cut_skip(
            video_path=video_path,
            source=source,
            state=state,
            tracker=tracker,
            message=t("task.cut.skip_invalid_range", {"start": start, "end": end, "title": title}),
            tracker_message=t("task.cut.skip_invalid_range_tracker", {"index": segment_number}),
        )
        return None

    try:
        media_dur = get_duration_sec(video_path, resolve_binary(ffprobe or "", "ffprobe"))
        if end > media_dur:
            print(t("task.cut.clamp_end", {"end": end, "duration": media_dur}))
            end = media_dur
    except _PROBE_FAILURES:
        pass
    if end <= start:
        _record_cut_skip(
            video_path=video_path,
            source=source,
            state=state,
            tracker=tracker,
            message=t("task.cut.skip_beyond_duration", {"start": start, "end": end, "title": title}),
            tracker_message=t("task.cut.skip_beyond_duration_tracker", {"index": segment_number}),
        )
        return None
    return plan_start, plan_end, start, end


def _apply_cut_offset(
    *,
    config: AppConfig,
    comp_dir: Path,
    idx: str,
    video_path: Path,
    ffprobe: str,
    source: str,
    project_selection: set[str] | None,
    start: float,
    end: float,
) -> tuple[float, float]:
    if source != "original":
        return start, end

    offset = 0.0
    text_json_paths = _indexed_files(config.texts_dir, idx, index_width=config.naming.index_width, suffix=".json")
    if text_json_paths:
        try:
            data = json.loads(text_json_paths[0].read_text(encoding="utf-8"))
            if isinstance(data, dict):
                offset = legacy_segment_offset_sec(load_identity(data))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
    if offset == 0.0:
        comp_candidates = [
            path
            for path in _indexed_files(comp_dir, idx, index_width=config.naming.index_width)
            if path.suffix.lower() in VIDEO_EXTS and _selected_cut_media(path, project_selection, artifact=True)
        ]
        if comp_candidates:
            offset = _compute_segment_offset(comp_candidates[0].stem, comp_dir, video_path, ffprobe)
    if offset:
        return start + offset, end + offset
    return start, end
