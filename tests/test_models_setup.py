from kageha.models.setup import PRESETS, list_presets, run_models_setup


def test_list_presets_includes_builtins():
    keys = {p.key for p in list_presets()}
    assert {"gemini", "openai", "anthropic", "siliconflow"} <= keys
    assert len(PRESETS) >= 4


def test_run_models_setup_writes_env_and_yaml(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    env = tmp_path / ".env"
    env.write_text("# test\n")
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    monkeypatch.setattr(
        "kageha.models.setup.resolve_env_file", lambda: env
    )
    monkeypatch.setattr(
        "kageha.models.setup.project_root", lambda: tmp_path
    )
    # 1 = gemini, defaults for model prompts, then skip smoke
    answers = iter(["1", "", "", "test-gemini-key", "", "n"])
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(answers))

    result = run_models_setup(smoke_test=False, skip_auth=True)
    assert result["ok"] is True
    assert "GEMINI_API_KEY=test-gemini-key" in env.read_text()
    yaml_text = (home / "models.yaml").read_text()
    assert "gemini" in yaml_text
    assert result["model_id"]
