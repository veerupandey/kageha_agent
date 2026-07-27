"""Guided first-run setup: surface → provider → packs → .env → next steps."""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any, Literal

from kageha.config import kageha_home, upsert_env_key
from kageha.models.registry import ModelRegistry
from kageha.models.setup import (
    ProviderPreset,
    _prompt,
    _prompt_secret,
    pin_roles,
    run_smoke,
)

Surface = Literal["chat", "webui", "both"]

_DEFAULT_ROLES = [
    "default",
    "planning",
    "coding",
    "fast_worker",
    "tool_calling",
    "monitor",
]

# First-run menu (Azure + OpenAI-compat called out explicitly).
_MENU: list[tuple[str, str]] = [
    ("gemini", "Google Gemini"),
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic"),
    ("azure", "Azure OpenAI"),
    ("compat", "Other OpenAI-compatible endpoint"),
]


def _yn(label: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    ans = _prompt(f"{label} [{hint}]", "Y" if default else "N").lower()
    if not ans:
        return default
    return ans in {"y", "yes"}


def _pick_surface() -> Surface:
    print(
        "\nWhere will you use Kageha?\n"
        "  1. Chat CLI (terminal)\n"
        "  2. WebUI (browser)\n"
        "  3. Both\n",
        flush=True,
    )
    while True:
        choice = _prompt("Choice", "1")
        if choice in {"1", "chat", "cli"}:
            return "chat"
        if choice in {"2", "webui", "web"}:
            return "webui"
        if choice in {"3", "both"}:
            return "both"
        print("Enter 1, 2, or 3.", flush=True)


def _pick_workspace() -> Path:
    default = Path.cwd().resolve()
    print(
        "\nProject folder for this agent (where .env is written).\n"
        f"Sessions and memory still live under {kageha_home()}.\n",
        flush=True,
    )
    raw = _prompt("Project folder", str(default))
    root = Path(raw).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _configure_provider(env_path: Path) -> dict[str, Any]:
    print(
        "\nHow will you connect a model?\n",
        flush=True,
    )
    for i, (_key, label) in enumerate(_MENU, 1):
        print(f"  {i}. {label}", flush=True)

    while True:
        choice = _prompt("Provider number", "1")
        try:
            n = int(choice)
        except ValueError:
            print("Enter a number.", flush=True)
            continue
        if 1 <= n <= len(_MENU):
            kind = _MENU[n - 1][0]
            break
        print("Out of range.", flush=True)

    if kind == "azure":
        return _configure_azure(env_path)
    if kind == "compat":
        return _configure_openai_compat(env_path)
    if kind == "gemini":
        preset = ProviderPreset(
            key="gemini",
            protocol="gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            api_key_env="GEMINI_API_KEY",
            default_model="gemini-3.6-flash",
            label="Google Gemini",
        )
    elif kind == "openai":
        preset = ProviderPreset(
            key="openai",
            protocol="openai_compat",
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
            default_model="gpt-4.1-mini",
            label="OpenAI",
        )
    else:
        preset = ProviderPreset(
            key="anthropic",
            protocol="anthropic_compat",
            base_url="https://api.anthropic.com",
            api_key_env="ANTHROPIC_API_KEY",
            default_model="claude-sonnet-4-20250514",
            label="Anthropic",
        )
    return _configure_preset(env_path, preset)


def _configure_preset(env_path: Path, preset: ProviderPreset) -> dict[str, Any]:
    model_api = _prompt("Model id (API model name)", preset.default_model)
    model_id = _prompt("Local model id (for /model)", f"{preset.key}-default")
    api_key = _prompt_secret("API key", preset.api_key_env)
    if not api_key:
        return {"ok": False, "error": "missing_api_key", "api_key_env": preset.api_key_env}
    upsert_env_key(preset.api_key_env, api_key, env_path)
    yaml_path = _write_model(
        model_id=model_id,
        protocol=preset.protocol,
        base_url=preset.base_url,
        api_key_env=preset.api_key_env,
        model=model_api,
        provider_name=preset.key,
    )
    return {
        "ok": True,
        "provider": preset.key,
        "model_id": model_id,
        "yaml_path": str(yaml_path),
        "api_key_env": preset.api_key_env,
    }


def _configure_azure(env_path: Path) -> dict[str, Any]:
    print(
        "\nAzure OpenAI — uses the OpenAI-compatible /openai/v1 path.\n",
        flush=True,
    )
    endpoint = _prompt(
        "Azure endpoint (e.g. https://NAME.openai.azure.com)",
        "",
    ).rstrip("/")
    if not endpoint:
        return {"ok": False, "error": "missing_azure_endpoint"}
    api_key = _prompt_secret("Azure API key", "AZURE_OPENAI_API_KEY")
    if not api_key:
        return {"ok": False, "error": "missing_api_key", "api_key_env": "AZURE_OPENAI_API_KEY"}
    api_version = _prompt("API version", "2024-12-01-preview")
    deployment = _prompt("Deployment name", "gpt-4.1-mini")
    model_id = _prompt("Local model id (for /model)", "azure-default")

    upsert_env_key("AZURE_OPENAI_API_KEY", api_key, env_path)
    upsert_env_key("AZURE_OPENAI_ENDPOINT", endpoint, env_path)
    upsert_env_key("AZURE_OPENAI_API_VERSION", api_version, env_path)
    upsert_env_key("AZURE_OPENAI_DEPLOYMENT", deployment, env_path)

    base_url = f"{endpoint}/openai/v1"
    yaml_path = _write_model(
        model_id=model_id,
        protocol="openai_compat",
        base_url=base_url,
        api_key_env="AZURE_OPENAI_API_KEY",
        model=deployment,
        provider_name="azure",
    )
    return {
        "ok": True,
        "provider": "azure",
        "model_id": model_id,
        "yaml_path": str(yaml_path),
        "api_key_env": "AZURE_OPENAI_API_KEY",
        "endpoint": endpoint,
        "deployment": deployment,
    }


def _configure_openai_compat(env_path: Path) -> dict[str, Any]:
    print(
        "\nOpenAI-compatible endpoint (vLLM, LiteLLM, Groq, OpenRouter, …).\n",
        flush=True,
    )
    base_url = _prompt("Base URL", "https://api.openai.com/v1").rstrip("/")
    api_key_env = _prompt("API key env var name", "OPENAI_API_KEY")
    api_key = _prompt_secret("API key", api_key_env)
    if not api_key:
        return {"ok": False, "error": "missing_api_key", "api_key_env": api_key_env}
    model_api = _prompt("Model id (API model name)", "gpt-4.1-mini")
    provider_name = _prompt("Provider key in models.yaml", "custom")
    model_id = _prompt(
        "Local model id (for /model)",
        model_api.split("/")[-1][:32] or "custom-default",
    )
    upsert_env_key(api_key_env, api_key, env_path)
    yaml_path = _write_model(
        model_id=model_id,
        protocol="openai_compat",
        base_url=base_url,
        api_key_env=api_key_env,
        model=model_api,
        provider_name=provider_name,
    )
    return {
        "ok": True,
        "provider": provider_name,
        "model_id": model_id,
        "yaml_path": str(yaml_path),
        "api_key_env": api_key_env,
        "base_url": base_url,
    }


def _write_model(
    *,
    model_id: str,
    protocol: str,
    base_url: str,
    api_key_env: str,
    model: str,
    provider_name: str,
) -> Path:
    yaml_path = kageha_home() / "models.yaml"
    reg = ModelRegistry.load()
    reg.add_model(
        model_id=model_id,
        protocol=protocol,
        base_url=base_url,
        api_key_env=api_key_env,
        model=model,
        roles=list(_DEFAULT_ROLES),
        provider_name=provider_name,
        path=yaml_path,
    )
    pin_roles(yaml_path, model_id, list(_DEFAULT_ROLES))
    return yaml_path


def _configure_packs(env_path: Path) -> list[str]:
    print(
        "\nOptional capabilities (core tools always load).\n"
        "  browser  — interactive Playwright / Comet\n"
        "  media    — Fal image/video (needs FAL_KEY)\n"
        "  computer — macOS desktop automation\n",
        flush=True,
    )
    packs: list[str] = []
    if _yn("Enable browser pack?", False):
        packs.append("browser")
    if _yn("Enable media pack (Fal)?", False):
        packs.append("media")
        fal = _prompt_secret("Fal API key (FAL_KEY or FAL_API_KEY)", "FAL_API_KEY")
        if fal:
            upsert_env_key("FAL_API_KEY", fal, env_path)
    if platform.system() == "Darwin":
        if _yn("Enable computer pack (macOS)?", False):
            packs.append("computer")
    else:
        print(
            f"(Skipping computer pack — this host is {platform.system()}, not macOS.)",
            flush=True,
        )
    if packs:
        upsert_env_key("KAGEHA_TOOL_PACKS", ",".join(packs), env_path)
    return packs


def _print_next_steps(
    *,
    surface: Surface,
    workspace: Path,
    packs: list[str],
    model_id: str | None,
) -> None:
    print("\nNext steps", flush=True)
    print("----------", flush=True)
    if surface in {"chat", "both"}:
        print(f"  cd {workspace}", flush=True)
        print("  uv run kageha chat", flush=True)
    if surface in {"webui", "both"}:
        print(
            "\n  # WebUI (build frontend once)\n"
            "  cd src/kageha/webui/frontend && npm install && npm run build && cd -\n"
            "  uv run kageha webui --open",
            flush=True,
        )
    if "browser" in packs:
        print(
            "\n  # Browser pack extras\n"
            "  uv sync --extra browser && uv run playwright install chromium",
            flush=True,
        )
    if "computer" in packs:
        print(
            "\n  # Computer pack (macOS)\n"
            "  ./scripts/install_computer_driver.sh\n"
            "  cua-driver permissions grant\n"
            "  uv sync --extra computer",
            flush=True,
        )
    print(
        "\nIn chat later:\n"
        "  /plan   clarify → research → plan.md → /build\n"
        "  /goal   execute now with Approve / Deny / Suggest\n"
        "  /normal everyday chat\n"
        "  /model  switch models"
        + (f" (you configured `{model_id}`)" if model_id else ""),
        flush=True,
    )
    if packs:
        print(f"\nPacks in .env: KAGEHA_TOOL_PACKS={','.join(packs)}", flush=True)
    print(flush=True)


def run_setup(*, smoke_test: bool | None = None) -> dict[str, Any]:
    """Interactive first-run wizard. Returns a summary dict for the CLI."""
    print(
        "\nKageha setup\n"
        "------------\n"
        "Answer a few prompts. Keys go in project .env;\n"
        "model registry goes in ~/.kageha/models.yaml.\n",
        flush=True,
    )
    surface = _pick_surface()
    workspace = _pick_workspace()
    env_path = workspace / ".env"
    if not env_path.is_file():
        env_path.write_text("# Kageha env\n", encoding="utf-8")

    provider = _configure_provider(env_path)
    if not provider.get("ok"):
        print(
            f"\nSetup stopped: {provider.get('error', 'unknown')}. "
            "Re-run `kageha setup` when you have a key.\n",
            flush=True,
        )
        return {"ok": False, **provider, "surface": surface, "workspace": str(workspace)}

    packs = _configure_packs(env_path)
    model_id = str(provider.get("model_id") or "")

    print(f"\nSaved key(s) → {env_path}", flush=True)
    print(f"Saved model `{model_id}` → {provider.get('yaml_path')}\n", flush=True)

    if smoke_test is None:
        smoke_test = _yn("Run a quick model smoke test now?", True)

    smoke_ok: bool | None = None
    smoke_error = ""
    if smoke_test and model_id:
        # Ensure process env sees the new keys for smoke.
        from dotenv import load_dotenv

        load_dotenv(env_path, override=True)
        smoke_ok, smoke_error = run_smoke(model_id)
        if smoke_ok:
            print(f"OK smoke test for {model_id}", flush=True)
        else:
            print(f"FAIL smoke test for {model_id}: {smoke_error}", flush=True)

    _print_next_steps(
        surface=surface,
        workspace=workspace,
        packs=packs,
        model_id=model_id or None,
    )
    return {
        "ok": True,
        "surface": surface,
        "workspace": str(workspace),
        "env_path": str(env_path),
        "yaml_path": provider.get("yaml_path"),
        "provider": provider.get("provider"),
        "model_id": model_id,
        "packs": packs,
        "smoke_ok": smoke_ok,
        "smoke_error": smoke_error,
    }
