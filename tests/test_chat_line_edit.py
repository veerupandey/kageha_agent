from kageha.chat.line_edit import history_path, remember, setup_line_editing


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
