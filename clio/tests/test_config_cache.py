"""Tests for clio/ui/services/config_cache.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from clio.ui.services.config_cache import ConfigCache

# ===========================================================================
# __init__
# ===========================================================================


class TestInit:
    def test_stores_attributes(self):
        def on_load(c):
            return None

        cache = ConfigCache(Path("/cfg.yaml"), maxsize=10, on_load=on_load)
        assert cache._config_path == Path("/cfg.yaml")
        assert cache._maxsize == 10
        assert cache._on_load is on_load
        assert cache._cache == {}
        assert cache._meta == {}
        assert cache._locks == {}
        assert cache._lock is not None


# ===========================================================================
# _fingerprint
# ===========================================================================


class TestFingerprint:
    def test_none_returns_zero(self):
        assert ConfigCache._fingerprint(None) == (0, 0)

    def test_nonexistent_path_returns_zero(self):
        assert ConfigCache._fingerprint(Path("/nonexistent/12345")) == (0, 0)

    def test_existing_path(self, tmp_path: Path):
        f = tmp_path / "test.yaml"
        f.write_text("key: val", encoding="utf-8")
        ns, size = ConfigCache._fingerprint(f)
        assert isinstance(ns, int)
        assert ns > 0
        assert isinstance(size, int)
        assert size > 0

    def test_falls_back_to_float_mtime(self, tmp_path: Path):
        """Filesystems without st_mtime_ns (simulated) still yield a usable fingerprint."""
        f = tmp_path / "test.yaml"
        f.write_text("key: val", encoding="utf-8")
        with patch("clio.ui.services.config_cache.Path.stat") as mock_stat:
            mock_stat.return_value = __import__("types").SimpleNamespace(st_mtime=123.4, st_size=8)
            assert ConfigCache._fingerprint(f) == (123_400_000_000, 8)


# ===========================================================================
# get — basic
# ===========================================================================


class TestGet:
    @patch("clio.ui.services.config_cache.load_config")
    def test_get_global_key(self, mock_load):
        mock_cfg = MagicMock()
        mock_load.return_value = mock_cfg
        cache = ConfigCache(None)
        result = cache.get(None)
        mock_load.assert_called_once_with("config.yaml", project_dir=None)
        assert result is not mock_cfg  # deep copy

    @patch("clio.ui.services.config_cache.load_config")
    def test_get_project_key(self, mock_load):
        mock_cfg = MagicMock()
        mock_load.return_value = mock_cfg
        cache = ConfigCache(None)
        proj = Path("/my/project")
        result = cache.get(proj)
        mock_load.assert_called_once_with("config.yaml", project_dir=proj)
        assert result is not mock_cfg

    @patch("clio.ui.services.config_cache.load_config")
    def test_returns_deep_copy(self, mock_load):
        """Each call should return a different object (deep copy)."""
        orig = MagicMock()
        orig.some_attr = "value"
        mock_load.return_value = orig
        cache = ConfigCache(None)
        r1 = cache.get(None)
        r2 = cache.get(None)
        assert r1 is not r2
        assert r1 is not orig

    @patch("clio.ui.services.config_cache.load_config")
    def test_on_load_called(self, mock_load):
        on_load = MagicMock()
        mock_cfg = MagicMock()
        mock_load.return_value = mock_cfg
        cache = ConfigCache(None, on_load=on_load)
        cache.get(None)
        on_load.assert_called_once_with(mock_cfg)

    @patch("clio.ui.services.config_cache.load_config")
    def test_on_load_not_called_on_cache_hit(self, mock_load):
        on_load = MagicMock()
        mock_cfg = MagicMock()
        mock_load.return_value = mock_cfg
        cache = ConfigCache(None, on_load=on_load)
        cache.get(None)
        cache.get(None)
        assert on_load.call_count == 1  # only called on first load


# ===========================================================================
# get — cache hit/miss based on mtime
# ===========================================================================


class TestCacheHitMiss:
    @patch("clio.ui.services.config_cache.load_config")
    def test_cache_hit_returns_cached(self, mock_load):
        """When config_path is None, _read_mtime always returns 0.0 → cache hit on 2nd call."""
        mock_cfg = MagicMock()
        mock_load.return_value = mock_cfg
        cache = ConfigCache(None)
        cache.get(None)
        cache.get(None)
        assert mock_load.call_count == 1

    @patch("clio.ui.services.config_cache.load_config")
    @patch.object(Path, "stat")
    def test_cache_miss_on_mtime_change(self, mock_stat, mock_load):
        mock_cfg = MagicMock()
        mock_load.return_value = mock_cfg
        mock_stat.return_value.st_mtime_ns = 100_000_000_000
        mock_stat.return_value.st_size = 10
        cache = ConfigCache(Path("/fake/config.yaml"))
        cache.get(None)  # loads once
        mock_stat.return_value.st_mtime_ns = 200_000_000_000  # mtime changes
        cache.get(None)  # reloads
        assert mock_load.call_count == 2

    @patch("clio.ui.services.config_cache.load_config")
    @patch.object(Path, "stat")
    def test_cache_miss_on_size_change(self, mock_stat, mock_load):
        """Same-size timestamp reuse is caught by the st_size portion of the fingerprint."""
        mock_cfg = MagicMock()
        mock_load.return_value = mock_cfg
        mock_stat.return_value.st_mtime_ns = 100_000_000_000
        mock_stat.return_value.st_size = 10
        cache = ConfigCache(Path("/fake/config.yaml"))
        cache.get(None)  # loads once
        mock_stat.return_value.st_size = 20  # size changes, mtime frozen
        cache.get(None)  # reloads
        assert mock_load.call_count == 2

    @patch("clio.ui.services.config_cache.load_config")
    def test_cache_miss_on_project_yaml_change(self, mock_load):
        """Simulate project.yaml mtime change by using config_path=None (all 0 mtimes)
        and checking that a different project_input triggers reload."""
        mock_cfg = MagicMock()
        mock_load.return_value = mock_cfg
        cache = ConfigCache(None)

        cache.get(None)
        cache.get(Path("/different/proj"))
        assert mock_load.call_count == 2

    @patch("clio.ui.services.config_cache.load_config")
    @patch.object(Path, "stat")
    def test_cache_hit_when_mtime_unchanged(self, mock_stat, mock_load):
        mock_cfg = MagicMock()
        mock_load.return_value = mock_cfg
        mock_stat.return_value.st_mtime_ns = 100_000_000_000
        mock_stat.return_value.st_size = 10
        cache = ConfigCache(Path("/fake/config.yaml"))
        cache.get(None)  # loads
        cache.get(None)  # cache hit
        assert mock_load.call_count == 1

    @patch("clio.ui.services.config_cache.load_config")
    @patch.object(Path, "stat")
    def test_project_key_cache_independence(self, mock_stat, mock_load):
        """Different project keys should not interfere."""
        mock_cfg_a = MagicMock()
        mock_cfg_b = MagicMock()
        mock_load.return_value = mock_cfg_a
        mock_stat.return_value.st_mtime_ns = 100_000_000_000
        mock_stat.return_value.st_size = 10
        cache = ConfigCache(Path("/fake/config.yaml"))
        cache.get(Path("/proj/a"))
        mock_load.return_value = mock_cfg_b
        cache.get(Path("/proj/b"))
        assert mock_load.call_count == 2


# ===========================================================================
# get — per-key locking
# ===========================================================================


class TestPerKeyLocking:
    def test_same_key_concurrent_get_loads_once(self):
        """Two threads asking for the same key must not load_config twice."""
        import threading
        import time

        mock_cfg = MagicMock()
        release = threading.Event()

        def slow_load(*_a, **_kw):
            release.wait(timeout=5)
            return mock_cfg

        with patch("clio.ui.services.config_cache.load_config", side_effect=slow_load) as mock_load:
            cache = ConfigCache(None)
            results: dict[int, object] = {}
            errors: list[BaseException] = []

            def worker(which: int):
                try:
                    results[which] = cache.get(None)
                except BaseException as e:  # noqa: BLE001 - surface thread errors
                    errors.append(e)

            t1 = threading.Thread(target=worker, args=(1,))
            t2 = threading.Thread(target=worker, args=(2,))
            t1.start()
            time.sleep(0.05)  # let worker 1 block inside load_config under the key lock
            t2.start()
            release.set()
            t1.join(timeout=5)
            t2.join(timeout=5)
            assert not t1.is_alive() and not t2.is_alive()
            assert errors == []
            assert mock_load.call_count == 1
            assert len(results) == 2

    def test_different_keys_do_not_serialize(self):
        """A slow load for one project must not block a different project."""
        import threading
        import time

        mock_cfg = MagicMock()
        release = threading.Event()
        block_once = {"pending": True}

        def slow_load(*_a, **_kw):
            if block_once["pending"]:
                block_once["pending"] = False
                release.wait(timeout=5)
            return mock_cfg

        with patch("clio.ui.services.config_cache.load_config", side_effect=slow_load) as mock_load:
            cache = ConfigCache(None)
            results: dict[str, object] = {}
            errors: list[BaseException] = []

            def worker1():
                try:
                    results["blocked"] = cache.get(Path("/proj/blocked"))
                except BaseException as e:  # noqa: BLE001 - surface thread errors
                    errors.append(e)

            def worker2():
                try:
                    results["fast"] = cache.get(Path("/proj/fast"))
                except BaseException as e:  # noqa: BLE001 - surface thread errors
                    errors.append(e)

            t1 = threading.Thread(target=worker1)
            t1.start()
            time.sleep(0.05)  # worker 1 is now blocked inside load_config holding its own key lock
            t2 = threading.Thread(target=worker2)
            t2.start()
            t2.join(timeout=5)
            assert not t2.is_alive()  # fast project completed while the other was blocked
            assert mock_load.call_count == 2
            assert results.get("fast") is not None
            release.set()
            t1.join(timeout=5)
            assert not t1.is_alive()
            assert errors == []
            assert results.get("blocked") is not None


# ===========================================================================
# invalidate_all / invalidate_key / keys
# ===========================================================================


class TestInvalidation:
    @patch("clio.ui.services.config_cache.load_config")
    def test_keys(self, mock_load):
        mock_load.return_value = MagicMock()
        cache = ConfigCache(None)
        assert cache.keys() == []
        cache.get(None)
        assert cache.keys() == ["__global__"]
        cache.get(Path("/proj/x"))
        keys = set(cache.keys())
        assert keys == {"__global__", str(Path("/proj/x").resolve())}

    @patch("clio.ui.services.config_cache.load_config")
    def test_invalidate_all(self, mock_load):
        mock_load.return_value = MagicMock()
        cache = ConfigCache(None)
        cache.get(None)
        cache.get(Path("/proj/x"))
        assert len(cache.keys()) == 2
        cache.invalidate_all()
        assert cache.keys() == []

    @patch("clio.ui.services.config_cache.load_config")
    def test_invalidate_key(self, mock_load):
        mock_load.return_value = MagicMock()
        cache = ConfigCache(None)
        cache.get(None)
        assert "__global__" in cache.keys()
        cache.invalidate_key("__global__")
        assert "__global__" not in cache.keys()

    @patch("clio.ui.services.config_cache.load_config")
    def test_invalidate_key_nonexistent(self, mock_load):
        """Removing a non-existent key should not raise."""
        mock_load.return_value = MagicMock()
        cache = ConfigCache(None)
        cache.invalidate_key("nope")  # should not raise
        assert cache.keys() == []


# ===========================================================================
# LRU eviction
# ===========================================================================


class TestLRUEviction:
    @patch("clio.ui.services.config_cache.load_config")
    def test_evicts_when_over_maxsize(self, mock_load):
        mock_cfg = MagicMock()
        mock_load.return_value = mock_cfg
        cache = ConfigCache(None, maxsize=2)

        p1 = Path("/proj/1")
        p2 = Path("/proj/2")
        p3 = Path("/proj/3")

        cache.get(p1)
        cache.get(p2)
        assert len(cache.keys()) == 2

        cache.get(p3)  # should evict p1
        keys = cache.keys()
        assert len(keys) == 2
        assert str(p1.resolve()) not in keys
        assert str(p2.resolve()) in keys
        assert str(p3.resolve()) in keys

    @patch("clio.ui.services.config_cache.load_config")
    def test_global_key_evicted_too(self, mock_load):
        mock_cfg = MagicMock()
        mock_load.return_value = mock_cfg
        cache = ConfigCache(None, maxsize=1)

        cache.get(None)  # fills cache
        cache.get(Path("/proj/x"))  # evicts global

        keys = cache.keys()
        assert "__global__" not in keys
        assert str(Path("/proj/x").resolve()) in keys

    @patch("clio.ui.services.config_cache.load_config")
    def test_cache_grows_under_maxsize(self, mock_load):
        mock_cfg = MagicMock()
        mock_load.return_value = mock_cfg
        cache = ConfigCache(None, maxsize=20)

        for i in range(10):
            cache.get(Path(f"/proj/{i}"))
        assert len(cache.keys()) == 10


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    @patch("clio.ui.services.config_cache.load_config")
    def test_reload_after_config_path_changes(self, mock_load):
        """If config_path is a real file and both cfg and proj mtimes are 0,
        the second call should still be a cache hit when config is None."""
        mock_cfg = MagicMock()
        mock_load.return_value = mock_cfg
        cache = ConfigCache(None)

        # Two requests with the same parameters
        r1 = cache.get(None)
        r2 = cache.get(None)
        assert mock_load.call_count == 1
        assert r1 is not r2  # deep copy

    @patch("clio.ui.services.config_cache.load_config")
    def test_meta_cleared_on_invalidate_all(self, mock_load):
        mock_cfg = MagicMock()
        mock_load.return_value = mock_cfg
        cache = ConfigCache(None)
        cache.get(None)
        assert len(cache._meta) == 1
        cache.invalidate_all()
        assert len(cache._meta) == 0
