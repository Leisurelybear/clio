from __future__ import annotations

from clio.plan_model import Plan
from clio.plan_readiness import check_plan_export_readiness


def _plan(seq, **kw):
    return Plan.from_dict({"day_title": "d", "sequence": seq, **kw})


def test_empty_sequence_error():
    r = check_plan_export_readiness(_plan([]), known_indices={"001"})
    assert not r.ok
    assert any(i.code == "sequence_empty" for i in r.errors)


def test_missing_index_error():
    r = check_plan_export_readiness(
        _plan([{"index": "099", "use_timeline": "00:00-00:05", "title": "t", "reason": "r"}]),
        known_indices={"001"},
    )
    assert any(i.code == "index_missing" for i in r.errors)


def test_offline_warning_only():
    r = check_plan_export_readiness(
        _plan([{"index": "001", "use_timeline": "00:00-00:05", "title": "t", "reason": "r"}]),
        known_indices={"001"},
        offline_indices={"001"},
    )
    assert r.ok
    assert any(i.code == "video_offline" for i in r.warnings)


def test_force_semantics_warnings_ok_errors_not():
    r = check_plan_export_readiness(
        _plan([{"index": "001", "use_timeline": "", "title": "", "reason": ""}]),
        known_indices={"001"},
    )
    assert r.ok
    assert r.warnings


def test_bad_timeline_error():
    r = check_plan_export_readiness(
        _plan([{"index": "001", "use_timeline": "nope", "title": "t", "reason": "r"}]),
        known_indices={"001"},
    )
    assert not r.ok
    assert any(i.code == "timeline_invalid" for i in r.errors)


def test_empty_known_indices_reports_missing_media():
    """A scanned-but-empty project means every referenced index is missing media."""
    r = check_plan_export_readiness(
        _plan([{"index": "001", "use_timeline": "00:00-00:05", "title": "t", "reason": "r"}]),
        known_indices=set(),
    )
    assert not r.ok
    assert any(i.code == "index_missing" for i in r.errors)


def test_none_known_indices_skips_index_missing():
    """None means the discovery simply hasn't run — do not guess missing."""
    r = check_plan_export_readiness(
        _plan([{"index": "001", "use_timeline": "00:00-00:05", "title": "t", "reason": "r"}]),
        known_indices=None,
    )
    assert r.ok
    assert not any(i.code == "index_missing" for i in r.errors)


def test_readiness_block_payload_errors_and_force():
    from clio.plan_model import PlanIssue
    from clio.plan_readiness import ReadinessResult, readiness_block_payload

    err = ReadinessResult(errors=[PlanIssue("error", "x", "bad")], warnings=[])
    assert readiness_block_payload(err, force=True) is not None
    assert readiness_block_payload(err, force=False) is not None

    warn = ReadinessResult(
        errors=[],
        warnings=[PlanIssue("warning", "w", "soft")],
    )
    blocked = readiness_block_payload(warn, force=False)
    assert blocked is not None
    assert blocked.get("needs_force") is True
    assert readiness_block_payload(warn, force=True) is None

    clean = ReadinessResult()
    assert readiness_block_payload(clean, force=False) is None


def test_collect_project_indices_from_compressed(tmp_path):
    from types import SimpleNamespace

    from clio.plan_readiness import collect_project_indices

    comp = tmp_path / "compressed"
    comp.mkdir()
    (comp / "001_CLIP.mp4").write_bytes(b"x")
    (comp / "002_CLIP.mp4").write_bytes(b"x")
    texts = tmp_path / "texts"
    texts.mkdir()
    (texts / "003_foo.json").write_text('{"index": "003"}', encoding="utf-8")
    cfg = SimpleNamespace(compressed_dir=comp, texts_dir=texts, project_dir=None)
    known, offline = collect_project_indices(cfg)
    assert "001" in known and "002" in known
    # Expanded forms so plan "001" matches compressed media that stored int 1 / "1"
    assert "1" in known and "2" in known
    # Text-only analyses must not make an unavailable index appear present.
    assert "003" not in known
    assert "3" not in known
    assert offline == set()


def test_index_padding_known_matches_plan_index():
    """Plan uses format_index '001'; texts may contribute '1' only."""
    from clio.plan_readiness import check_plan_export_readiness, expand_index_keys

    assert "001" in expand_index_keys(1)
    assert "1" in expand_index_keys("001")
    r = check_plan_export_readiness(
        _plan([{"index": "001", "use_timeline": "00:00-00:05", "title": "t", "reason": "r"}]),
        known_indices={"1"},  # only unpadded form
    )
    assert r.ok
    assert not any(i.code == "index_missing" for i in r.errors)


def test_offline_index_padding_warning():
    r = check_plan_export_readiness(
        _plan([{"index": "001", "use_timeline": "00:00-00:05", "title": "t", "reason": "r"}]),
        known_indices={"001"},
        offline_indices={"1"},
    )
    assert r.ok
    assert any(i.code == "video_offline" for i in r.warnings)


def test_collect_offline_from_videos_json(tmp_path):
    import json
    from types import SimpleNamespace

    from clio.plan_readiness import collect_project_indices

    texts = tmp_path / "texts"
    texts.mkdir()
    (texts / "001_title.json").write_text(
        json.dumps(
            {
                "index": 1,
                "media_identity": {
                    "original_stem": "GL010683",
                    "original_path": "",
                    "compressed_stem": "001_GL010683",
                    "compressed_path": "",
                    "index": "001",
                },
            }
        ),
        encoding="utf-8",
    )
    missing = tmp_path / "missing" / "GL010683.mp4"
    # do not create the file
    (tmp_path / "videos.json").write_text(json.dumps([str(missing)]), encoding="utf-8")
    cfg = SimpleNamespace(
        compressed_dir=tmp_path / "compressed",  # missing dir ok
        texts_dir=texts,
        project_dir=tmp_path,
        naming=SimpleNamespace(index_width=3),
    )
    known, offline = collect_project_indices(cfg)
    assert known == set()
    assert offline  # mapped via original_stem
    assert "001" in offline or "1" in offline


def test_text_only_index_is_missing_media_for_readiness(tmp_path):
    import json
    from types import SimpleNamespace

    from clio.plan_readiness import check_plan_export_readiness, collect_project_indices

    texts = tmp_path / "texts"
    texts.mkdir()
    (texts / "003_missing.json").write_text(json.dumps({"index": 3}), encoding="utf-8")
    cfg = SimpleNamespace(
        compressed_dir=tmp_path / "compressed",
        texts_dir=texts,
        project_dir=None,
        naming=SimpleNamespace(index_width=3),
    )

    known, offline = collect_project_indices(cfg)
    result = check_plan_export_readiness(
        _plan([{"index": "003", "use_timeline": "00:00-00:05", "title": "t", "reason": "r"}]),
        known_indices=known,
        offline_indices=offline,
    )

    assert not result.ok
    assert any(issue.code == "index_missing" for issue in result.errors)


def test_collect_project_indices_respects_videos_selection(tmp_path):
    import json
    from types import SimpleNamespace

    from clio.plan_readiness import collect_project_indices

    comp = tmp_path / "compressed"
    comp.mkdir()
    (comp / "001_A.mp4").write_bytes(b"x")
    (comp / "002_B.mp4").write_bytes(b"x")
    texts = tmp_path / "texts"
    texts.mkdir()
    (texts / "001_A.json").write_text(json.dumps({"index": 1, "source_file": "A.mp4"}), encoding="utf-8")
    (texts / "002_B.json").write_text(json.dumps({"index": 2, "source_file": "B.mp4"}), encoding="utf-8")
    videos = tmp_path / "videos"
    videos.mkdir()
    selected = videos / "A.mp4"
    selected.write_bytes(b"x")
    (videos / "videos.json").write_text(json.dumps([str(selected)]), encoding="utf-8")
    cfg = SimpleNamespace(
        compressed_dir=comp,
        texts_dir=texts,
        project_dir=videos,
        naming=SimpleNamespace(index_width=3),
    )

    known, offline = collect_project_indices(cfg)

    assert "001" in known
    assert "002" not in known
    assert offline == set()


def test_removed_video_index_fails_readiness(tmp_path):
    import json
    from types import SimpleNamespace

    from clio.plan_readiness import check_plan_export_readiness, collect_project_indices

    comp = tmp_path / "compressed"
    comp.mkdir()
    (comp / "001_A.mp4").write_bytes(b"x")
    texts = tmp_path / "texts"
    texts.mkdir()
    (texts / "001_A.json").write_text(json.dumps({"index": 1, "source_file": "A.mp4"}), encoding="utf-8")
    videos = tmp_path / "videos"
    videos.mkdir()
    selected = videos / "A.mp4"
    selected.write_bytes(b"x")
    (videos / "videos.json").write_text(json.dumps([str(selected)]), encoding="utf-8")
    cfg = SimpleNamespace(
        compressed_dir=comp,
        texts_dir=texts,
        project_dir=videos,
        naming=SimpleNamespace(index_width=3),
    )

    known, _ = collect_project_indices(cfg)
    result = check_plan_export_readiness(
        _plan([{"index": "002", "use_timeline": "00:00-00:05", "title": "removed", "reason": "r"}]),
        known_indices=known,
    )

    assert not result.ok
    assert any(issue.code == "index_missing" for issue in result.errors)


def test_original_offline_is_error_but_compressed_is_warning():
    plan = _plan([{"index": "001", "use_timeline": "00:00-00:05", "title": "t", "reason": "r"}])

    compressed = check_plan_export_readiness(
        plan,
        known_indices={"001"},
        offline_indices={"001"},
        source="compressed",
    )
    original = check_plan_export_readiness(
        plan,
        known_indices={"001"},
        offline_indices={"001"},
        source="original",
    )

    assert compressed.ok
    assert any(issue.code == "video_offline" for issue in compressed.warnings)
    assert not original.ok
    assert any(issue.code == "video_offline" for issue in original.errors)


def test_timeline_out_of_bounds_and_overlap_are_reported():
    plan = _plan(
        [
            {"index": "001", "use_timeline": "00:00-00:08", "title": "first", "reason": "r"},
            {"index": "001", "use_timeline": "00:06-00:12", "title": "second", "reason": "r"},
        ]
    )

    result = check_plan_export_readiness(
        plan,
        known_indices={"001"},
        media_durations={"001": 10.0},
    )

    assert any(issue.code == "timeline_out_of_bounds" for issue in result.errors)
    assert any(issue.code == "timeline_overlap" for issue in result.warnings)


def test_collect_project_media_durations_prefers_vmeta(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from clio.plan_readiness import collect_project_media_durations

    compressed = tmp_path / "compressed"
    compressed.mkdir()
    clip = compressed / "001_clip.mp4"
    clip.write_bytes(b"x")
    clip.with_suffix(".vmeta").write_text(
        json.dumps(
            {
                "source_path": str(tmp_path / "clip.mp4"),
                "target_path": clip.name,
                "source_modifyTime": 0,
                "source_size": 1,
                "target_modifyTime": 0,
                "target_size": 1,
                "source_duration_sec": 12.0,
                "target_duration_sec": 10.0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("clio.plan_readiness.get_duration_sec", lambda *args: 99.0)
    cfg = SimpleNamespace(
        compressed_dir=compressed,
        project_dir=None,
        naming=SimpleNamespace(index_width=3),
        paths=SimpleNamespace(ffprobe=""),
    )

    assert collect_project_media_durations(cfg) == {"001": 10.0}


def test_collect_project_indices_ignores_same_named_unselected_original(tmp_path):
    import json
    from types import SimpleNamespace

    from clio.plan_readiness import collect_project_indices
    from clio.vmeta import VideoMeta

    comp = tmp_path / "compressed"
    comp.mkdir()
    a_dir = tmp_path / "videos" / "a"
    b_dir = tmp_path / "videos" / "b"
    a_dir.mkdir(parents=True)
    b_dir.mkdir(parents=True)
    a = a_dir / "clip.mp4"
    b = b_dir / "clip.mp4"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    comp_a = comp / "001_clip.mp4"
    comp_b = comp / "002_clip.mp4"
    comp_a.write_bytes(b"ca")
    comp_b.write_bytes(b"cb")
    VideoMeta.build(a, comp_a, 10.0, 10.0).write(comp_a)
    VideoMeta.build(b, comp_b, 10.0, 10.0).write(comp_b)
    (tmp_path / "videos" / "videos.json").write_text(json.dumps([str(a)]), encoding="utf-8")

    cfg = SimpleNamespace(
        compressed_dir=comp,
        texts_dir=tmp_path / "texts",
        project_dir=tmp_path / "videos",
        naming=SimpleNamespace(index_width=3),
    )

    known, offline = collect_project_indices(cfg)

    assert "001" in known
    assert "002" not in known
    assert offline == set()


def test_quality_warning_when_plan_range_outside_analysis():
    plan = _plan(
        [
            {
                "index": "001",
                "use_timeline": "00:04-00:10",
                "title": "t",
                "reason": "r",
                "voiceover_hint": "v",
            }
        ],
        total_estimated_sec=6,
    )

    result = check_plan_export_readiness(
        plan,
        known_indices={"001"},
        media_durations={"001": 20},
        analysis_timelines={"1": [(0.0, 5.0)]},
    )

    assert any(issue.code == "timeline_outside_analysis" for issue in result.warnings)


def test_quality_warning_for_large_analysis_gap():
    plan = _plan(
        [
            {
                "index": "001",
                "use_timeline": "00:00-00:10",
                "title": "t",
                "reason": "r",
                "voiceover_hint": "v",
            }
        ],
        total_estimated_sec=10,
    )

    result = check_plan_export_readiness(
        plan,
        known_indices={"001"},
        media_durations={"001": 90},
        analysis_timelines={"001": [(0.0, 10.0), (80.0, 90.0)]},
    )

    assert any(issue.code == "analysis_timeline_gap" for issue in result.warnings)


def test_quality_warning_for_duration_mismatch_and_empty_voiceover():
    plan = _plan(
        [
            {
                "index": "001",
                "use_timeline": "00:00-00:10",
                "title": "t",
                "reason": "r",
                "voiceover_hint": "",
            }
        ],
        total_estimated_sec=180,
    )

    result = check_plan_export_readiness(
        plan,
        known_indices={"001"},
        media_durations={"001": 20},
        analysis_timelines={"1": [(0.0, 20.0)]},
    )

    assert any(issue.code == "duration_mismatch" for issue in result.warnings)
    assert any(issue.code == "voiceover_hint_empty" for issue in result.warnings)


def test_quality_missing_and_empty_analysis_timelines():
    plan = _plan(
        [
            {
                "index": "001",
                "use_timeline": "00:00-00:10",
                "title": "t",
                "reason": "r",
                "voiceover_hint": "v",
            },
            {
                "index": "002",
                "use_timeline": "00:00-00:10",
                "title": "t",
                "reason": "r",
                "voiceover_hint": "v",
            },
        ],
        total_estimated_sec=20,
    )

    result = check_plan_export_readiness(
        plan,
        known_indices={"001", "002"},
        analysis_timelines={"2": []},
    )

    assert any(issue.code == "analysis_timeline_missing" for issue in result.warnings)
    assert any(issue.code == "analysis_timeline_empty" for issue in result.warnings)


def test_quality_analysis_timeline_out_of_bounds():
    plan = _plan(
        [
            {
                "index": "001",
                "use_timeline": "00:00-00:10",
                "title": "t",
                "reason": "r",
                "voiceover_hint": "v",
            }
        ],
        total_estimated_sec=10,
    )

    result = check_plan_export_readiness(
        plan,
        known_indices={"001"},
        media_durations={"001": 20},
        analysis_timelines={"1": [(0.0, 30.0)]},
    )

    assert any(issue.code == "analysis_timeline_out_of_bounds" for issue in result.warnings)


def test_fully_grounded_plan_has_no_quality_warnings():
    plan = _plan(
        [
            {
                "index": "001",
                "use_timeline": "00:00-01:40",
                "title": "t",
                "reason": "r",
                "voiceover_hint": "v",
            }
        ],
        total_estimated_sec=100,
    )

    result = check_plan_export_readiness(
        plan,
        known_indices={"001"},
        media_durations={"001": 120},
        analysis_timelines={"1": [(0.0, 120.0)]},
    )

    assert result.warnings == []


def test_collect_project_analysis_timelines_merges_and_preserves_empty(tmp_path):
    import json
    from types import SimpleNamespace

    from clio.plan_readiness import collect_project_analysis_timelines

    texts = tmp_path / "texts"
    texts.mkdir()
    (texts / "001_a.json").write_text(
        json.dumps(
            {
                "index": "001",
                "timeline": [
                    {"start": "00:00", "end": "00:05"},
                    {"start": 5, "end": 10},
                    {"start": "00:15", "end": "00:20"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (texts / "001_b.json").write_text(
        json.dumps({"index": 1, "timeline": [{"start": 20, "end": 30}]}),
        encoding="utf-8",
    )
    (texts / "002_empty.json").write_text(json.dumps({"index": 2, "timeline": []}), encoding="utf-8")

    cfg = SimpleNamespace(texts_dir=texts, project_dir=None, naming=SimpleNamespace(index_width=3))

    assert collect_project_analysis_timelines(cfg) == {
        "1": [(0.0, 10.0), (15.0, 30.0)],
        "2": [],
    }


def test_collect_project_indices_maps_legacy_analysis_index_to_current_compressed(tmp_path):
    import json
    from types import SimpleNamespace

    from clio.plan_readiness import collect_project_indices

    comp = tmp_path / "compressed"
    comp.mkdir()
    (comp / "197_GX011275.mp4").write_bytes(b"x")
    texts = tmp_path / "texts"
    texts.mkdir()
    (texts / "016_title.json").write_text(
        json.dumps(
            {
                "index": "016",
                "media_identity": {
                    "original_stem": "GX011275",
                    "compressed_stem": "016_GX011275",
                    "index": "016",
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(
        compressed_dir=comp,
        texts_dir=texts,
        project_dir=None,
        naming=SimpleNamespace(index_width=3),
    )

    known, offline = collect_project_indices(cfg)

    assert "197" in known
    assert "016" in known
    assert "16" in known
    assert offline == set()


def test_collect_project_media_durations_maps_legacy_analysis_index_to_current_compressed(tmp_path):
    import json
    from types import SimpleNamespace

    from clio.plan_readiness import collect_project_media_durations
    from clio.vmeta import VideoMeta

    source = tmp_path / "GX011275.mp4"
    source.write_bytes(b"source")
    comp = tmp_path / "compressed"
    comp.mkdir()
    clip = comp / "197_GX011275.mp4"
    clip.write_bytes(b"compressed")
    VideoMeta.build(source, clip, 20.0, 12.0).write(clip)
    texts = tmp_path / "texts"
    texts.mkdir()
    (texts / "016_title.json").write_text(
        json.dumps(
            {
                "index": "016",
                "media_identity": {
                    "original_stem": "GX011275",
                    "compressed_stem": "016_GX011275",
                    "index": "016",
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(
        compressed_dir=comp,
        texts_dir=texts,
        project_dir=None,
        naming=SimpleNamespace(index_width=3),
        paths=SimpleNamespace(ffprobe=""),
    )

    durations = collect_project_media_durations(cfg)

    assert durations["197"] == 12.0
    assert durations["016"] == 12.0


def test_collect_project_indices_rejects_legacy_alias_with_multiple_owners(tmp_path):
    import json
    from types import SimpleNamespace

    from clio.plan_readiness import collect_project_indices

    comp = tmp_path / "compressed"
    comp.mkdir()
    (comp / "197_GX011275.mp4").write_bytes(b"a")
    (comp / "198_GX011275.mp4").write_bytes(b"b")
    texts = tmp_path / "texts"
    texts.mkdir()
    (texts / "016_title.json").write_text(
        json.dumps(
            {
                "index": "016",
                "media_identity": {
                    "original_stem": "GX011275",
                    "compressed_stem": "016_GX011275",
                    "index": "016",
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(
        compressed_dir=comp,
        texts_dir=texts,
        project_dir=None,
        naming=SimpleNamespace(index_width=3),
    )

    known, _ = collect_project_indices(cfg)

    assert "197" in known
    assert "198" in known
    assert "016" not in known
    assert "16" not in known


def test_duration_short_warning_uses_target_floor():
    plan = _plan([{"index": "001", "use_timeline": "00:00-00:20", "title": "t", "reason": "r"}], total_estimated_sec=68)

    plain = check_plan_export_readiness(plan, known_indices={"001"})
    assert not any(i.code == "duration_short" for i in plain.warnings)

    targeted = check_plan_export_readiness(plan, known_indices={"001"}, target_duration_sec=180)
    short = [i for i in targeted.warnings if i.code == "duration_short"]
    assert short and "144.0" in short[0].message


def test_duration_short_warning_keeps_floor_without_target():
    plan = _plan([{"index": "001", "use_timeline": "00:00-00:20", "title": "t", "reason": "r"}], total_estimated_sec=25)

    result = check_plan_export_readiness(plan, known_indices={"001"})

    assert any(i.code == "duration_short" for i in result.warnings)


def _paced_segments(count: int, timeline: str) -> list[dict[str, str]]:
    return [{"index": "001", "use_timeline": timeline, "title": "t", "reason": "r"} for _ in range(count)]


def test_readiness_flags_fragmented_narrative_segments():
    plan = _plan(_paced_segments(12, "00:00-00:05"), total_estimated_sec=60)

    result = check_plan_export_readiness(plan, known_indices={"001"}, target_duration_sec=180)

    codes = [issue.code for issue in result.warnings]
    assert "duration_short" in codes
    assert "duration_fragments" in codes
    fragment = next(issue for issue in result.warnings if issue.code == "duration_fragments")
    # The opening segment is hook-exempt, so 11 of the 12 segments are fragments.
    assert "11" in fragment.message


def test_readiness_fragment_warning_needs_more_than_quarter_of_segments():
    within_tolerance = _plan(
        _paced_segments(7, "00:00-00:30") + _paced_segments(2, "00:00-00:05"),
        total_estimated_sec=220,
    )
    beyond_tolerance = _plan(
        _paced_segments(6, "00:00-00:30") + _paced_segments(3, "00:00-00:05"),
        total_estimated_sec=195,
    )

    within = check_plan_export_readiness(within_tolerance, known_indices={"001"}, target_duration_sec=180)
    beyond = check_plan_export_readiness(beyond_tolerance, known_indices={"001"}, target_duration_sec=180)

    assert not any(issue.code == "duration_fragments" for issue in within.warnings)
    assert any(issue.code == "duration_fragments" for issue in beyond.warnings)


def test_readiness_allows_short_broll_inserts():
    sequence = _paced_segments(5, "00:00-00:30") + _paced_segments(4, "00:00-00:02")
    plan = _plan(sequence, total_estimated_sec=158)

    result = check_plan_export_readiness(plan, known_indices={"001"}, target_duration_sec=180)

    assert not any(issue.code == "duration_fragments" for issue in result.warnings)
    assert not any(issue.code == "duration_short" for issue in result.warnings)
