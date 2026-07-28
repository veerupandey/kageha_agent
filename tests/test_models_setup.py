from kageha.models.setup import PRESETS, list_presets


def test_list_presets_includes_builtins():
    keys = {p.key for p in list_presets()}
    assert {"gemini", "openai", "anthropic", "siliconflow"} <= keys
    assert len(PRESETS) >= 4


def test_models_setup_aliases_kageha_setup():
    from kageha.cli import models_setup

    assert callable(models_setup)
    assert "alias" in (models_setup.__doc__ or "").lower() or "kageha setup" in (
        models_setup.__doc__ or ""
    ).lower()
