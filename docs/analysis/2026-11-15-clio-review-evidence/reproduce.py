"""Offline review probes. Assertions pin observed defects, not desired behavior.

Run from the repository root with the project's Python interpreter.
Only temporary files and patched provider boundaries are used.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from clio.ai import factory
from clio.ai.base import TokenUsage
from clio.ai.token_usage import FileTokenUsageStore
from clio.config.models import AppConfig, GlobalConfig, ProjectConfig, ProviderConfig
from clio.main import run_check
from clio.tasks._helpers import ClipRecord
from clio.tasks.compress import _compress_settings_fingerprint, _find_reusable, _meta_matches_settings, _write_vindex
from clio.ui.routes.export import _copy_draft_to_jianying
from clio.ui.routes.plan import handle_put_plan
from clio.ui.routes.refine import handle_post_refine
from clio.ui.services.file_service import content_version, resolve_in, resolve_texts, save_with_version
from clio.ui.services.run_preview import _compress_step
from clio.utils import validate_within_root
from clio.vmeta import VideoIndex, VideoMeta


def config(root: Path) -> AppConfig:
    project = ProjectConfig()
    project.paths.output_dir = root / "output"
    return AppConfig(global_cfg=GlobalConfig(), project_cfg=project, project_dir=root)


def handler_for(path: Path, cfg: AppConfig) -> MagicMock:
    handler = MagicMock()
    handler._resolve_project_dir.return_value = cfg.project_dir
    handler._get_project_output.return_value = cfg.paths.output_dir
    handler._get_config.return_value = cfg
    handler._resolve_texts.return_value = path
    handler._resolve_in.return_value = path
    return handler


def probes(root: Path) -> list[dict]:
    results = []
    cfg = config(root)
    cfg.compressed_dir.mkdir(parents=True)
    source = root / "source.mp4"
    source.write_bytes(b"source")
    compressed = cfg.compressed_dir / "001_source.mp4"
    compressed.write_bytes(b"compressed")
    old_settings = _compress_settings_fingerprint(cfg)
    meta = VideoMeta.build(source, compressed, 10, 10, old_settings)
    meta.write(compressed)
    cfg.project_cfg.compress.max_width += 2
    preview = _compress_step(cfg, [source], force=False)
    with patch("clio.tasks.compress.get_duration_sec", return_value=10):
        runtime = _find_reusable(source, {f"src:{source.resolve()}": [(1, compressed, meta)]}, cfg, "ffprobe")
    assert preview["will_skip"] == 1 and runtime[1] is None
    results.append({"id": "R01", "observed": "preview skips a runtime-stale compressed file"})

    cfg.project_cfg.compress.max_width -= 2
    cfg.global_cfg.compress.codec = "libx265"
    cfg.project_cfg.compress.target_size_mb = 0
    crf_settings = _compress_settings_fingerprint(cfg)
    crf_meta = VideoMeta.build(source, compressed, 10, 10, crf_settings)
    cfg.global_cfg.compress.crf += 1
    cfg.global_cfg.compress.codec = "libx264"
    assert _compress_settings_fingerprint(cfg) == crf_settings and _meta_matches_settings(crf_meta, cfg)
    results.append({"id": "R02", "observed": "codec and CRF changes leave compression cache valid"})

    cfg.texts_dir.mkdir()
    artifact = cfg.texts_dir / "001_old.json"
    artifact.write_text(json.dumps({"title": "old", "timeline": []}), encoding="utf-8")
    original_version = content_version(artifact.read_bytes())
    handler = handler_for(artifact, cfg)

    def provider_result(data, *_args, **_kwargs):
        save_with_version(artifact, b'{"title":"manual edit","timeline":[]}', original_version)
        return {**data, "title": "AI result"}

    with patch("clio.ui.routes.refine.refine_text", side_effect=provider_result):
        handle_post_refine(handler, {}, {"file": artifact.name, "type": "texts"})
    assert json.loads(artifact.read_text(encoding="utf-8"))["title"] == "AI result"
    assert handler._send_json.call_args.args[0]["ok"] is True
    assert not artifact.with_suffix(".txt").exists()
    results.append({"id": "R03", "observed": "refine overwrites a concurrent edit and does not create TXT"})

    cfg.script.scripts_subdir = "custom-scripts"
    cfg.analyze.texts_subdir = "custom-texts"
    cfg.scripts_dir.mkdir()
    cfg.texts_dir.mkdir()
    (cfg.scripts_dir / "001_voiceover.json").write_text("{}", encoding="utf-8")
    (cfg.texts_dir / "001_analysis.json").write_text("{}", encoding="utf-8")
    assert resolve_in("scripts", "001_voiceover.json", cfg.paths.output_dir, cfg.paths.output_dir) is None
    assert resolve_texts("001_analysis.json", cfg.paths.output_dir, cfg.paths.output_dir) is None
    results.append({"id": "R07", "observed": "configured custom artifacts exist but Web resolvers return None"})

    outside = root / "outside.json"
    outside.write_text('{"title":"outside"}', encoding="utf-8")
    default_scripts = cfg.paths.output_dir / "scripts"
    default_scripts.mkdir()
    link = default_scripts / "linked.json"
    try:
        link.symlink_to(outside)
    except OSError:
        results.append({"id": "R08", "observed": "SKIPPED: file symlink creation unavailable"})
    else:
        assert resolve_in("scripts", link.name, cfg.paths.output_dir, cfg.paths.output_dir) == link
        assert json.loads(link.read_text(encoding="utf-8"))["title"] == "outside"
        try:
            validate_within_root(link, cfg.paths.output_dir)
        except ValueError:
            pass
        else:
            raise AssertionError("reference containment helper should reject the link")
        results.append(
            {"id": "R08", "observed": "Web script resolver follows a symlink rejected by containment helper"}
        )

    generated = root / "generated"
    target_base = root / "jianying"
    generated.mkdir()
    target_base.mkdir()
    for name in ("draft_content.json", "draft_meta_info.json", "subtitles.srt"):
        (generated / name).write_text("new output", encoding="utf-8")
    target = _copy_draft_to_jianying(generated, str(target_base), "day1")
    assert target is not None
    assert sorted(p.name for p in target.iterdir()) == ["draft_content.json"]
    (target / "draft_content.json").write_text("user editing work", encoding="utf-8")
    _copy_draft_to_jianying(generated, str(target_base), "day1")
    assert (target / "draft_content.json").read_text(encoding="utf-8") == "new output"
    assert not list(target.glob("*.bak*"))
    results.extend(
        [
            {"id": "R09", "observed": "auto-copy omits draft metadata and subtitle sidecar"},
            {"id": "R10", "observed": "second auto-copy overwrites an edited destination without backup"},
        ]
    )

    provider = MagicMock()
    factory._clear_provider_cache()
    factory._provider_cache[("review",)] = factory._CachedEntry(provider, 0)
    with factory.provider_use(provider):
        factory._clear_provider_cache()
        assert provider.close.call_count == 1
    results.append({"id": "R11", "observed": "cache clear closes a provider while provider_use is active"})

    usage_dir = root / "usage"
    usage_dir.mkdir()
    stores = [FileTokenUsageStore(str(usage_dir)), FileTokenUsageStore(str(usage_dir))]
    barrier = threading.Barrier(2)
    errors = []
    for store in stores:
        read = store._read_raw

        def synchronized_read(read=read):
            raw = read()
            barrier.wait(timeout=5)
            return raw

        store._read_raw = synchronized_read

    def record(store):
        try:
            store.record("voiceover", "fake", TokenUsage(1, 1, 2))
        except Exception as exc:  # noqa: BLE001 - surface worker failures in the probe's main assertion
            errors.append(str(exc))

    workers = [threading.Thread(target=record, args=(store,)) for store in stores]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        assert not worker.is_alive()
    assert not errors, errors
    stats = json.loads((usage_dir / ".token_usage.json").read_text(encoding="utf-8"))
    assert len(stats["history"]) == 1
    results.append({"id": "R12", "observed": "two successful independent usage writers persist only one call"})

    cfg.plans_dir.mkdir()
    plan_json = cfg.plans_dir / "day1_plan.json"
    plan_md = cfg.plans_dir / "day1_plan.md"
    plan_json.write_text('{"day_title":"old","sequence":[]}', encoding="utf-8")
    plan_md.write_text("# old\n", encoding="utf-8")
    handler = handler_for(plan_json, cfg)
    handler.headers = {}
    handle_put_plan(handler, {"day": ["day1"]}, {"day_title": "new", "sequence": []})
    assert json.loads(plan_json.read_text(encoding="utf-8"))["day_title"] == "new"
    assert plan_md.read_text(encoding="utf-8") == "# old\n"
    results.append({"id": "R13", "observed": "plan PUT changes JSON but leaves Markdown unchanged"})

    cfg.global_cfg.ai.providers = {"local": ProviderConfig(name="local", type="ollama")}
    cfg.project_cfg.ai.tasks = {"voiceover": SimpleNamespace(provider="local", model="fake")}
    with (
        patch("clio.main.load_config", return_value=cfg),
        patch("clio.main.is_virtualenv_python", return_value=True),
        patch("clio.main.discover_ffmpeg_bin", return_value="fake-binary"),
        patch("clio.tasks._video_loader.source_videos", return_value=[]),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        assert run_check(root / "unused.yaml") == 1
    results.append({"id": "R14", "observed": "CLI check rejects credential-free Ollama binding"})

    records = []
    for number, camera in enumerate(("camera-a", "camera-b"), 1):
        camera_dir = root / camera
        camera_dir.mkdir()
        original = camera_dir / "shared.mp4"
        original.write_bytes(camera.encode())
        target_file = cfg.compressed_dir / f"{number:03d}_shared.mp4"
        target_file.write_bytes(b"compressed")
        file_meta = VideoMeta.build(original, target_file, 10, 10)
        records.append(ClipRecord(number, "shared", original, compressed_path=target_file, meta=file_meta))
    _write_vindex(records, cfg, "unused")
    remaining_index = VideoIndex.read("shared", cfg.compressed_dir)
    assert remaining_index is not None
    assert remaining_index.source_path == str(records[1].source_path.resolve())
    assert [s.filename for s in remaining_index.segments] == ["002_shared.mp4"]
    results.append({"id": "R15", "observed": "same-basename source videos overwrite the first source's vindex"})

    precise_source = root / "precise.mp4"
    precise_source.write_bytes(b"before")
    second_ns = 1_800_000_000_000_000_000
    os.utime(precise_source, ns=(second_ns + 100_000_000, second_ns + 100_000_000))
    precise_meta = VideoMeta.build(precise_source, compressed, 10, 10)
    precise_source.write_bytes(b"after!")
    os.utime(precise_source, ns=(second_ns + 900_000_000, second_ns + 900_000_000))
    assert not precise_meta.is_stale(precise_source, compressed)
    results.append({"id": "R16", "observed": "same-size source changes inside one second do not invalidate vmeta"})
    return results


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="clio-review-") as tmp:
        observed = probes(Path(tmp))
    print(json.dumps({"probe_count": len(observed), "results": observed}, ensure_ascii=True, indent=2))
