from pathlib import Path

from kageha.models.registry import ModelRegistry
from kageha.models.router import ModelRouter

_REPO_MODELS = Path(__file__).resolve().parents[1] / "models.yaml"


def test_load_models_yaml():
    reg = ModelRegistry.load()
    assert "gemini" in reg.providers
    assert "siliconflow" in reg.providers
    assert "gemini-flash" in reg.models
    assert "default" in reg.roles or "fast_worker" in reg.roles


def test_primary_and_worker_role_ladders():
    # Pin to repo models.yaml so ~/.kageha overrides do not hide defaults.
    reg = ModelRegistry.load(_REPO_MODELS)
    # Prefer native tool-capable API models; Antigravity is text-only fallback.
    assert reg.roles["default"][0] == "gemini-flash"
    assert "antigravity" in reg.roles["default"] or "antigravity-flash" in reg.roles["default"]
    assert reg.roles["planning"][0] in {"kimi-plan", "gemini-pro", "gemini-flash"}
    # Tool loops must lead with API models that declare tool_calling.
    assert reg.roles["tool_calling"][0] == "gemini-flash"
    assert "tool_calling" not in (reg.models["antigravity-flash"].capabilities or [])
    assert reg.roles["fast_worker"][0] == "gemini-flash"


def test_anti_retry_ledger_does_not_poison_later_requests(monkeypatch):
    reg = ModelRegistry.load()
    router = ModelRouter(reg)
    router.anti_retry.add(("run-1", "kimi-plan", "hard_fail"))
    fake = object()

    monkeypatch.setattr(
        reg,
        "available_models",
        lambda: [reg.models["kimi-plan"], reg.models["gemini-pro"]],
    )
    monkeypatch.setattr(reg, "build", lambda model_id: fake)

    assert router.pick("tool_calling", task_id="run-1") is fake
