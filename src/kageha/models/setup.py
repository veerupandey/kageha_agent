"""Provider presets and shared helpers used by ``kageha setup``.

Guided setup lives in ``kageha.setup_wizard`` — this module keeps presets,
prompts, role pinning, and smoke tests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from kageha.config import (
    models_yaml_paths,
    read_env_value,
)
from kageha.models.registry import ModelRegistry


@dataclass(frozen=True)
class ProviderPreset:
    key: str
    protocol: str
    base_url: str
    api_key_env: str
    default_model: str
    label: str


# Sensible first-run presets (aligned with repo models.yaml).
PRESETS: list[ProviderPreset] = [
    ProviderPreset(
        key="gemini",
        protocol="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key_env="GEMINI_API_KEY",
        default_model="gemini-3.6-flash",
        label="Google Gemini",
    ),
    ProviderPreset(
        key="openai",
        protocol="openai_compat",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        default_model="gpt-4.1-mini",
        label="OpenAI",
    ),
    ProviderPreset(
        key="anthropic",
        protocol="anthropic_compat",
        base_url="https://api.anthropic.com",
        api_key_env="ANTHROPIC_API_KEY",
        default_model="claude-sonnet-4-20250514",
        label="Anthropic",
    ),
    ProviderPreset(
        key="siliconflow",
        protocol="openai_compat",
        base_url="https://api.siliconflow.com/v1",
        api_key_env="SILICONFLOW_API_KEY",
        default_model="moonshotai/Kimi-K3",
        label="SiliconFlow (Kimi / OpenAI-compat)",
    ),
]


def list_presets() -> list[ProviderPreset]:
    """Presets plus any providers already declared in models.yaml paths."""
    by_key = {p.key: p for p in PRESETS}
    for path in models_yaml_paths():
        if not path.is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except Exception:  # noqa: BLE001
            continue
        providers = data.get("providers") or {}
        models = data.get("models") or []
        defaults: dict[str, str] = {}
        for m in models:
            if isinstance(m, dict) and m.get("provider") and m.get("model"):
                defaults.setdefault(str(m["provider"]), str(m["model"]))
        for name, cfg in providers.items():
            if not isinstance(cfg, dict) or name in by_key:
                continue
            by_key[name] = ProviderPreset(
                key=str(name),
                protocol=str(cfg.get("protocol") or "openai_compat"),
                base_url=str(cfg.get("base_url") or ""),
                api_key_env=str(cfg.get("api_key_env") or f"{name.upper()}_API_KEY"),
                default_model=defaults.get(str(name), "default"),
                label=str(name),
            )
    return list(by_key.values())


def _prompt(label: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    raw = input(f"{label}{hint}: ").strip()
    return raw or default


def _prompt_secret(label: str, env_key: str) -> str:
    existing = os.environ.get(env_key) or read_env_value(env_key) or ""
    masked = ""
    if existing:
        masked = existing[:4] + "…" if len(existing) > 4 else "****"
    raw = _prompt(f"{label} (leave blank to keep existing {masked})" if masked else label)
    return raw or existing


def pin_roles(path: Path, model_id: str, roles: list[str]) -> None:
    """Pin model_id first on each role ladder in models.yaml."""
    data: dict[str, Any] = {}
    if path.is_file():
        data = yaml.safe_load(path.read_text()) or {}
    ladder = data.setdefault("roles", {})
    for role in roles:
        existing = list(ladder.get(role) or [])
        ladder[role] = [model_id] + [x for x in existing if x != model_id]
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def run_smoke(model_id: str) -> tuple[bool, str]:
    """One-shot chat smoke for a configured model id."""
    import asyncio

    async def _go() -> tuple[bool, str]:
        try:
            reg = ModelRegistry.load()
            model = reg.build(model_id)
            text = await model.smoke()
            if not text:
                return False, "empty response"
            return True, text[:80]
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    return asyncio.run(_go())


# Back-compat aliases for older call sites / tests.
_pin_roles = pin_roles
_run_smoke = run_smoke
