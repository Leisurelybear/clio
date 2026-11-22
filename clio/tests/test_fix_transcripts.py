"""Regression tests for transcript write concurrency and validation."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from clio.ui.routes import transcripts as transcripts_mod
from clio.ui.routes.transcripts import handle_put_transcripts


def _handler(tmp_path: Path) -> MagicMock:
    handler = MagicMock()
    handler._get_project_output.return_value = tmp_path
    handler._resolve_project_dir.return_value = tmp_path
    cfg = MagicMock()
    cfg.whisper.transcripts_subdir = "transcripts"
    handler._get_config.return_value = cfg
    return handler


def test_concurrent_put_with_same_revision_yields_one_conflict(tmp_path: Path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    tp = tdir / "clip_transcript.json"
    tp.write_text(json.dumps({"_rev": 0, "segments": [{"start": 0, "end": 1, "text": "a"}]}), encoding="utf-8")

    real_read = transcripts_mod._read_transcript

    def slow_read(path):
        result = real_read(path)
        time.sleep(0.2)  # widen the read -> write window
        return result

    statuses: list[int] = []
    lock = threading.Lock()

    def worker(text: str) -> None:
        handler = _handler(tmp_path)
        handle_put_transcripts(handler, {"video": ["clip.mp4"]}, {"segment_index": 0, "text": text, "revision": 0})
        args = handler._send_json.call_args.args
        with lock:
            statuses.append(args[1] if len(args) > 1 else 200)

    with patch.object(transcripts_mod, "_read_transcript", side_effect=slow_read):
        threads = [threading.Thread(target=worker, args=(name,)) for name in ("first", "second")]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10)

    assert sorted(statuses) == [200, 409]
    assert json.loads(tp.read_text(encoding="utf-8"))["_rev"] == 1
