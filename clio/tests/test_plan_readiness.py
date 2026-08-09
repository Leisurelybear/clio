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
    assert "001" in known and "002" in known and "003" in known
    # Expanded forms so plan "001" matches texts that stored int 1 / "1"
    assert "1" in known and "2" in known and "3" in known
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
    assert "001" in known or "1" in known
    assert offline  # mapped via original_stem
    assert "001" in offline or "1" in offline
