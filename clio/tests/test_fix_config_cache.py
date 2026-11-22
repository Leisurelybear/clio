"""Regression tests: ConfigCache evicts the least recently *used* entry."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from clio.ui.services.config_cache import ConfigCache


def test_cache_hit_refreshes_lru_position(tmp_path: Path):
    dirs = {name: tmp_path / name for name in ("a", "b", "c")}
    for d in dirs.values():
        d.mkdir()
    loads: list[Path | None] = []

    def fake_load(_path, project_dir=None):
        loads.append(project_dir)
        cfg = MagicMock()
        cfg.ui.language = None
        return cfg

    cache = ConfigCache(tmp_path / "config.yaml", maxsize=2)
    with (
        patch("clio.ui.services.config_cache.load_config", side_effect=fake_load),
        patch("clio.ui.services.config_cache.copy.deepcopy", side_effect=lambda obj: obj),
    ):
        cache.get(dirs["a"])
        cache.get(dirs["b"])
        cache.get(dirs["a"])  # hit: a becomes most recently used
        cache.get(dirs["c"])  # evicts b, not a
        cache.get(dirs["a"])  # still cached

    assert loads == [dirs["a"], dirs["b"], dirs["c"]]
    assert str(dirs["b"].resolve()) not in cache.keys()
