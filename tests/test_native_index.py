"""Tests for kageha.native index façade (Python fallback + flag)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kageha.native import (
    index_backend,
    native_index_available,
    native_index_enabled,
    rank_paths,
)
from kageha.native.index import reset_native_index_probe_for_tests
from kageha.project.file_index import FileIndex, reset_file_indexes_for_tests


@pytest.fixture(autouse=True)
def _reset():
    reset_file_indexes_for_tests()
    reset_native_index_probe_for_tests()
    yield
    reset_file_indexes_for_tests()
    reset_native_index_probe_for_tests()


def test_default_backend_is_python_without_rust():
    assert native_index_available() is False
    assert index_backend() == "python"


def test_native_index_env_forces_python(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KAGEHA_NATIVE_INDEX", "0")
    assert native_index_enabled() is False
    assert index_backend() == "python"


def test_rank_paths_python_fallback_matches_file_index(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "server.py").write_text("x\n", encoding="utf-8")
    (root / "README.md").write_text("#\n", encoding="utf-8")

    idx = FileIndex(root)
    idx.rebuild()
    hits = idx.query("server", limit=5)
    assert hits
    assert hits[0]["path"].endswith("server.py")

    entries = [(f.path, f.mtime) for f in idx._files]
    ranked = rank_paths(entries, "server", limit=5)
    assert ranked[0]["path"] == hits[0]["path"]
