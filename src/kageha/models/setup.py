"""Interactive provider / model setup wizard (Hermes ``hermes model`` analogue)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from kageha.config import (
    kageha_home,
    models_yaml_paths,
    project_root,
    read_env_value,
    resolve_env_file,
    upsert_env_key,
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


def run_models_setup(
    *,
    smoke_test: bool | None = None,
    skip_auth: bool = False,
) -> dict[str, Any]:
    """Interactive wizard: subscription auth → provider → .env + models.yaml → smoke.

    Returns a summary dict for the CLI.
    """
    auth_step: dict[str, Any] = {}
    if not skip_auth:
        from kageha.models.auth_cli import run_model_auth_setup_step

        auth_step = run_model_auth_setup_step(interactive=True)
        # If user imported ChatGPT/Gemini OAuth and wants to stop at subscription-only
        if auth_step.get("imported") and not any(
            x.startswith("GEMINI") or x.startswith("OPENAI")
            for x in auth_step.get("imported") or []
        ):
            cont = _prompt(
                "Subscription auth imported. Also configure an API-key provider? [y/N]",
                "N",
            ).lower()
            if cont not in {"y", "yes"}:
                return {
                    "ok": True,
                    "auth": auth_step,
                    "model_id": None,
                    "provider": None,
                    "smoke_ok": None,
                    "note": "Using imported subscription auth; skip API key setup.",
                }

    presets = list_presets()
    print(
        "\nKageha model setup\n"
        "------------------\n"
        "Configure an API provider (keys go in .env; registry in ~/.kageha/models.yaml).\n"
        "Subscription auth: kageha models auth import chatgpt|gemini-cli\n"
        "Session switching still uses /model in chat.\n",
        flush=True,
    )
    for i, p in enumerate(presets, 1):
        has = bool(os.environ.get(p.api_key_env) or read_env_value(p.api_key_env))
        flag = "✓" if has else "·"
        print(f"  {i}. {flag} {p.label}  ({p.api_key_env})", flush=True)
    print(f"  {len(presets) + 1}. Custom OpenAI-compatible endpoint", flush=True)

    while True:
        choice = _prompt("Provider number", "1")
        try:
            n = int(choice)
        except ValueError:
            print("Enter a number.", flush=True)
            continue
        if 1 <= n <= len(presets):
            preset = presets[n - 1]
            custom = False
            break
        if n == len(presets) + 1:
            preset = ProviderPreset(
                key="custom",
                protocol="openai_compat",
                base_url="",
                api_key_env="OPENAI_API_KEY",
                default_model="",
                label="Custom",
            )
            custom = True
            break
        print("Out of range.", flush=True)

    if custom:
        base_url = _prompt("Base URL", "https://api.openai.com/v1")
        api_key_env = _prompt("API key env var name", "OPENAI_API_KEY")
        protocol = _prompt("Protocol (openai_compat|anthropic_compat|gemini)", "openai_compat")
        default_model = _prompt("Model id (API model name)", "gpt-4.1-mini")
        provider_name = _prompt("Provider key in models.yaml", "custom")
        model_id = _prompt("Local model id (for /model)", default_model.split("/")[-1][:32])
    else:
        base_url = preset.base_url
        api_key_env = preset.api_key_env
        protocol = preset.protocol
        default_model = _prompt("Model id (API model name)", preset.default_model)
        provider_name = preset.key
        model_id = _prompt("Local model id (for /model)", preset.key + "-default")

    api_key = _prompt_secret("API key value", api_key_env)
    if not api_key:
        print(
            f"No key for {api_key_env}. Set it in .env and re-run `kageha models setup`.",
            flush=True,
        )
        return {"ok": False, "error": "missing_api_key", "api_key_env": api_key_env}

    env_path = upsert_env_key(api_key_env, api_key, resolve_env_file())
    roles_raw = _prompt("Roles (comma-separated)", "default,fast_worker,tool_calling")
    roles = [r.strip() for r in roles_raw.split(",") if r.strip()] or ["default"]

    reg = ModelRegistry.load()
    yaml_path = reg.add_model(
        model_id=model_id,
        protocol=protocol,
        base_url=base_url,
        api_key_env=api_key_env,
        model=default_model,
        roles=roles,
        provider_name=provider_name,
        path=kageha_home() / "models.yaml",
    )

    # Pin as first on matching role ladders in the user overlay.
    _pin_roles(yaml_path, model_id, roles)

    print(f"\nSaved key → {env_path}", flush=True)
    print(f"Saved model `{model_id}` → {yaml_path}\n", flush=True)

    if smoke_test is None:
        ans = _prompt("Run smoke test now? [Y/n]", "Y").lower()
        smoke_test = ans in {"", "y", "yes"}

    smoke_ok: bool | None = None
    smoke_error = ""
    if smoke_test:
        smoke_ok, smoke_error = _run_smoke(model_id)
        if smoke_ok:
            print(f"OK smoke test for {model_id}", flush=True)
        else:
            print(f"FAIL smoke test for {model_id}: {smoke_error}", flush=True)

    return {
        "ok": True,
        "auth": auth_step,
        "model_id": model_id,
        "provider": provider_name,
        "env_path": str(env_path),
        "yaml_path": str(yaml_path),
        "smoke_ok": smoke_ok,
        "smoke_error": smoke_error,
        "project_root": str(project_root()),
    }


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
