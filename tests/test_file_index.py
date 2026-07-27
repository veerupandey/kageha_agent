"""Tests for project file index (ignore rules + ranking + Web UI route)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from kageha.app_server import AppServer
from kageha.memory.service import reset_memory_service_for_tests
from kageha.project.file_index import (
    FileIndex,
    GitIgnoreFilter,
    get_file_index,
    reset_file_indexes_for_tests,
    score_path,
)
from kageha.webui.server import WebUIApp


@pytest.fixture(autouse=True)
def _clear_index_cache():
    reset_file_indexes_for_tests()
    yield
    reset_file_indexes_for_tests()


@pytest.fixture()
def webui_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WebUIApp:
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    monkeypatch.setenv("KAGEHA_MEMORY_EMBEDDINGS", "off")
    reset_memory_service_for_tests()
    app = WebUIApp(AppServer(), project_root=str(tmp_path / "proj"))
    yield app
    app.close()
    reset_memory_service_for_tests()


def _call(
    app: WebUIApp,
    method: str,
    path: str,
    *,
    query: dict[str, list[str]] | None = None,
) -> tuple[int, dict]:
    status, data, ctype = app.handle(method, path, query or {}, b"", None)
    assert "json" in ctype
    return status, json.loads(data.decode("utf-8"))


def _touch_tree(root: Path) -> None:
    (root / "src" / "kageha" / "webui").mkdir(parents=True)
    (root / "src" / "kageha" / "webui" / "server.py").write_text("x\n", encoding="utf-8")
    (root / "src" / "kageha" / "project" / "file_index.py").parent.mkdir(parents=True)
    (root / "src" / "kageha" / "project" / "file_index.py").write_text("y\n", encoding="utf-8")
    (root / "README.md").write_text("# hi\n", encoding="utf-8")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "index.js").write_text("z\n", encoding="utf-8")
    (root / "dist" / "bundle.js").parent.mkdir(parents=True)
    (root / "dist" / "bundle.js").write_text("b\n", encoding="utf-8")
    (root / ".git" / "objects").mkdir(parents=True)
    (root / ".git" / "objects" / "pack").write_text("g\n", encoding="utf-8")
    (root / "__pycache__" / "x.cpython-312.pyc").parent.mkdir(parents=True)
    (root / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"\0")
    (root / ".venv" / "lib").mkdir(parents=True)
    (root / ".venv" / "lib" / "site.py").write_text("s\n", encoding="utf-8")
    (root / "build" / "out.txt").parent.mkdir(parents=True)
    (root / "build" / "out.txt").write_text("o\n", encoding="utf-8")
    (root / "secrets.log").write_text("nope\n", encoding="utf-8")
    (root / ".gitignore").write_text("*.log\nlocal/\n", encoding="utf-8")
    (root / "local" / "scratch.txt").parent.mkdir(parents=True)
    (root / "local" / "scratch.txt").write_text("scratch\n", encoding="utf-8")


def test_noise_dirs_and_gitignore_skipped(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    _touch_tree(root)

    idx = FileIndex(root)
    n = idx.rebuild()
    paths = {f.path for f in idx._files}

    assert n >= 3
    assert "README.md" in paths
    assert "src/kageha/webui/server.py" in paths
    assert "node_modules/pkg/index.js" not in paths
    assert "dist/bundle.js" not in paths
    assert ".git/objects/pack" not in paths
    assert "__pycache__/x.cpython-312.pyc" not in paths
    assert ".venv/lib/site.py" not in paths
    assert "build/out.txt" not in paths
    assert "secrets.log" not in paths
    assert "local/scratch.txt" not in paths


def test_gitignore_negation_and_filter_helpers(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".gitignore").write_text("*.tmp\n!keep.tmp\n", encoding="utf-8")
    (root / "drop.tmp").write_text("d\n", encoding="utf-8")
    (root / "keep.tmp").write_text("k\n", encoding="utf-8")
    (root / "ok.txt").write_text("o\n", encoding="utf-8")

    filt = GitIgnoreFilter(root)
    assert filt.ignore_file("drop.tmp", "drop.tmp") is True
    assert filt.ignore_file("keep.tmp", "keep.tmp") is False
    assert filt.ignore_dir("node_modules", "node_modules") is True

    idx = FileIndex(root)
    idx.rebuild()
    paths = {f.path for f in idx._files}
    assert "ok.txt" in paths
    assert "keep.tmp" in paths
    assert "drop.tmp" not in paths


def test_ranking_filename_beats_path_substring():
    now = time.time()
    server = score_path("src/kageha/webui/server.py", "server", mtime=now, now=now)
    nested = score_path(
        "vendor/server/legacy/notes.md", "server", mtime=now - 3600, now=now
    )
    assert server > nested

    exact = score_path("server.py", "server.py", mtime=now, now=now)
    partial = score_path("my_server_util.py", "server", mtime=now, now=now)
    assert exact > partial

    assert score_path("readme.md", "zzzz-nope", mtime=now, now=now) < 0


def test_query_orders_and_limits(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    _touch_tree(root)
    # Extra distractor with "server" in a deep path.
    deep = root / "docs" / "ops" / "server-notes.md"
    deep.parent.mkdir(parents=True)
    deep.write_text("notes\n", encoding="utf-8")

    idx = FileIndex(root)
    idx.warm()
    hits = idx.query("server", limit=5)
    assert hits
    assert hits[0]["path"].endswith("server.py")
    assert all("score" in h and "path" in h for h in hits)
    assert len(hits) <= 5

    empty = idx.query("", limit=2)
    assert len(empty) == 2


def test_kageha_project_root_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "from-env"
    root.mkdir()
    (root / "env_only.py").write_text("e\n", encoding="utf-8")
    monkeypatch.setenv("KAGEHA_PROJECT_ROOT", str(root))
    idx = get_file_index(None)
    assert idx.root == root.resolve()
    paths = [h["path"] for h in idx.query("env", limit=10)]
    assert "env_only.py" in paths


def test_api_project_files_route(webui_app: WebUIApp, tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    _touch_tree(root)

    status, payload = _call(
        webui_app,
        "GET",
        "/api/project/files",
        query={"q": ["server"], "limit": ["10"], "project_root": [str(root)]},
    )
    assert status == 200
    assert payload["q"] == "server"
    assert payload["total_indexed"] >= 3
    assert payload["truncated"] is False
    assert isinstance(payload["files"], list)
    assert payload["files"][0]["path"].endswith("server.py")
    assert "score" in payload["files"][0]
    # Noise must not appear
    paths = {f["path"] for f in payload["files"]}
    assert not any(p.startswith("node_modules/") for p in paths)
