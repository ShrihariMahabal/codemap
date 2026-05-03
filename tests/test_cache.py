"""Tests for codemap.cache — content-addressable extraction caching."""
import json
import tempfile
from pathlib import Path

from codemap.cache import clear_cache, file_hash, load_cached, save_cached


class TestFileHash:
    """Tests for the SHA256 hashing function."""

    def test_same_content_same_hash(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False,
        ) as f:
            f.write("print('hello')")
            f.flush()
            h1 = file_hash(Path(f.name), Path(f.name).parent)
            h2 = file_hash(Path(f.name), Path(f.name).parent)
        assert h1 == h2

    def test_different_content_different_hash(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False,
        ) as f1:
            f1.write("print('hello')")
            f1.flush()
            h1 = file_hash(Path(f1.name), Path(f1.name).parent)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False,
        ) as f2:
            f2.write("print('world')")
            f2.flush()
            h2 = file_hash(Path(f2.name), Path(f2.name).parent)

        assert h1 != h2

    def test_hash_is_hex_string(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False,
        ) as f:
            f.write("x = 1")
            f.flush()
            h = file_hash(Path(f.name), Path(f.name).parent)
        assert len(h) == 64  # SHA256 hex digest
        assert all(c in "0123456789abcdef" for c in h)


class TestCacheRoundTrip:
    """Tests for save_cached / load_cached round-trip."""

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Create a source file
            src = root / "test.py"
            src.write_text("x = 1")

            result = {"nodes": [{"id": "test_x"}], "edges": []}
            save_cached(src, result, root)
            loaded = load_cached(src, root)

            assert loaded is not None
            assert loaded["nodes"] == result["nodes"]

    def test_cache_miss_on_change(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            src = root / "test.py"
            src.write_text("x = 1")

            result = {"nodes": [{"id": "test_x"}], "edges": []}
            save_cached(src, result, root)

            # Modify the file
            src.write_text("x = 2")
            loaded = load_cached(src, root)

            assert loaded is None  # Cache miss — content changed

    def test_cache_miss_when_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            src = root / "test.py"
            src.write_text("x = 1")

            loaded = load_cached(src, root)
            assert loaded is None

    def test_clear_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            src = root / "test.py"
            src.write_text("x = 1")

            save_cached(src, {"nodes": [], "edges": []}, root)
            count = clear_cache(root)

            assert count >= 1
            assert load_cached(src, root) is None
