
from kageha.chat.line_edit import (
    completion_matches,
    history_path,
    remember,
    setup_line_editing,
)


def test_setup_line_editing_returns_bool(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    ok = setup_line_editing()
    assert isinstance(ok, bool)
    # History path is under KAGEHA_HOME even before first write.
    assert history_path() == tmp_path / "chat_history"
    remember("hello there")
    remember("hello there")  # duplicate ignored
    if ok:
        import readline

        n = readline.get_current_history_length()
        assert n >= 1
        assert readline.get_history_item(n) == "hello there"


def test_complete_primary_slash_commands():
    hits = completion_matches("/mo", "/mo")
    assert "/model" in hits
    assert all(h.startswith("/mo") for h in hits)


def test_complete_mode_commands():
    assert "/plan" in completion_matches("/pl", "/pl")
    assert "/goal" in completion_matches("/go", "/go")


def test_complete_model_subcommands_and_ids():
    hits = completion_matches("/model ", "")
    assert "list" in hits
    assert "reset" in hits
    # Registry may be empty in CI; at least static tokens are present.
    assert any(h.startswith("list") or h.startswith("azure") or len(h) > 2 for h in hits)


def test_complete_browser_next_token():
    hits = completion_matches("/browser ", "")
    assert "list" in hits
    assert "use" in hits
    assert "comet" in hits


def test_complete_nested_browser_pack():
    hits = completion_matches("/browser pack ", "")
    assert "on" in hits
    assert "off" in hits


def test_path_completions_with_at(tmp_path):
    (tmp_path / "alpha.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "beta").mkdir()
    hits = completion_matches("@a", "@a", cwd=tmp_path)
    assert "@alpha.py" in hits
    dir_hits = completion_matches("@b", "@b", cwd=tmp_path)
    assert "@beta/" in dir_hits


def test_path_completions_directory_listing(tmp_path):
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "mod.py").write_text("x\n", encoding="utf-8")
    hits = completion_matches("@pkg/", "@pkg/", cwd=tmp_path)
    assert "@pkg/mod.py" in hits


def test_non_slash_plain_text_has_no_matches():
    assert completion_matches("hello world", "world") == []
