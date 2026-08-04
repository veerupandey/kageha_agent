"""Guided setup: surface → connection → packs → pin default → smoke.

``kageha setup`` is the single guided entry point. Re-running overwrites
params this wizard owns (provider keys written this run, KAGEHA_TOOL_PACKS,
and global model pins).
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any, Literal

from kageha.chat.model_commands import persist_setup_model_pins
from kageha.config import kageha_home, read_env_value, upsert_env_key
from kageha.harness.tools.media import FAL_IMAGE_MODELS, default_fal_image_model
from kageha.models.oauth_setup import (
    detect_tools,
    setup_antigravity_oauth,
    setup_codex_oauth,
)
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

# Connection menu (API keys + subscription OAuth).
_MENU: list[tuple[str, str]] = [
    ("gemini", "Google Gemini (API key)"),
    ("openai", "OpenAI (API key)"),
    ("anthropic", "Anthropic (API key)"),
    ("azure", "Azure OpenAI"),
    ("compat", "Other OpenAI-compatible endpoint"),
    ("codex_oauth", "OpenAI Codex OAuth (ChatGPT `codex login`)"),
    ("antigravity_oauth", "Antigravity / Gemini CLI OAuth (Google)"),
    ("both_oauth", "Both OAuth (Codex + Antigravity)"),
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
        f"Sessions and memory still live under {kageha_home()}.\n"
        "Re-running setup overwrites keys and packs in this .env.\n",
        flush=True,
    )
    raw = _prompt("Project folder", str(default))
    root = Path(raw).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _print_oauth_detect() -> None:
    tools = detect_tools()
    print(
        "Detected local logins:\n"
        f"  codex CLI:    {'yes' if tools['has_codex_cli'] else 'no'}"
        f"  auth.json: {'yes' if tools['chatgpt_codex_cli'] else 'no'}\n"
        f"  agy CLI:      {'yes' if tools['has_agy_cli'] else 'no'}\n"
        f"  gemini CLI:   {'yes' if tools['has_gemini_cli'] else 'no'}"
        f"  oauth_creds: {'yes' if tools['gemini_cli_oauth'] else 'no'}",
        flush=True,
    )


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

    if kind == "codex_oauth":
        return _configure_oauth("codex")
    if kind == "antigravity_oauth":
        return _configure_oauth("antigravity")
    if kind == "both_oauth":
        return _configure_oauth("both")
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


def _configure_oauth(target: str) -> dict[str, Any]:
    """Launch Codex and/or Antigravity OAuth; pick default model."""
    _print_oauth_detect()
    imported: list[str] = []
    auth: dict[str, Any] = {}

    if target in {"codex", "both"}:
        codex = setup_codex_oauth(launch_login=True)
        auth["codex"] = codex
        if codex.get("imported"):
            imported.append("chatgpt")

    if target in {"antigravity", "both"}:
        anti = setup_antigravity_oauth(launch_login=True)
        auth["antigravity"] = anti
        if anti.get("imported"):
            imported.append("antigravity")

    if not imported:
        return {
            "ok": False,
            "error": "oauth_import_failed",
            "auth": auth,
        }

    if target == "codex" or (
        target == "both" and "chatgpt" in imported and "antigravity" not in imported
    ):
        model_id = "gpt-codex"
        provider = "openai-codex"
    elif target == "antigravity" or (
        target == "both" and "antigravity" in imported and "chatgpt" not in imported
    ):
        model_id = "antigravity"
        provider = "antigravity"
    else:
        # Both imported — ask which is the session default.
        print(
            "\nBoth OAuth profiles imported. Default model?\n"
            "  1. gpt-codex (OpenAI Codex / ChatGPT)\n"
            "  2. antigravity (Google)\n",
            flush=True,
        )
        choice = _prompt("Choice", "1")
        if choice in {"2", "antigravity", "agy"}:
            model_id = "antigravity"
            provider = "antigravity"
        else:
            model_id = "gpt-codex"
            provider = "openai-codex"

    return {
        "ok": True,
        "provider": provider,
        "model_id": model_id,
        "yaml_path": str(kageha_home() / "models.yaml"),
        "api_key_env": None,
        "auth": auth,
        "imported": imported,
        "oauth_only": True,
    }


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
    existing_endpoint = read_env_value("AZURE_OPENAI_ENDPOINT", env_path) or ""
    endpoint = _prompt(
        "Azure endpoint (e.g. https://NAME.openai.azure.com)",
        existing_endpoint,
    ).rstrip("/")
    if not endpoint:
        return {"ok": False, "error": "missing_azure_endpoint"}
    api_key = _prompt_secret("Azure API key", "AZURE_OPENAI_API_KEY")
    if not api_key:
        return {"ok": False, "error": "missing_api_key", "api_key_env": "AZURE_OPENAI_API_KEY"}
    api_version = _prompt(
        "API version",
        read_env_value("AZURE_OPENAI_API_VERSION", env_path) or "2024-12-01-preview",
    )
    deployment = _prompt(
        "Deployment name",
        read_env_value("AZURE_OPENAI_DEPLOYMENT", env_path) or "gpt-4.1-mini",
    )
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


def _read_setup_pins() -> dict[str, str]:
    path = kageha_home() / "models.yaml"
    if not path.is_file():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text()) or {}
    except Exception:  # noqa: BLE001
        return {}
    pins = data.get("setup_pins") if isinstance(data, dict) else None
    if not isinstance(pins, dict):
        return {}
    return {str(k): str(v) for k, v in pins.items() if v}


def _configure_role_models(session_default: str) -> dict[str, str]:
    """Ask for planner + executor/subagent models (overwrite role pins)."""
    reg = ModelRegistry.load()
    known = sorted(reg.models.keys())
    prev = _read_setup_pins()
    planner_default = prev.get("planner") or session_default
    executor_default = prev.get("executor") or session_default

    print(
        "\nRole models (overwrite previous pins)\n"
        "-------------------------------------\n"
        "  planner  — Plan mode / planning role\n"
        "  executor — tool loops, coding, subagents\n"
        f"Known ids (sample): {', '.join(known[:8])}{'…' if len(known) > 8 else ''}\n",
        flush=True,
    )
    planner = _prompt("Planner model id", planner_default).strip() or planner_default
    executor = (
        _prompt("Executor / subagents model id", executor_default).strip()
        or executor_default
    )

    if planner not in reg.models:
        print(
            f"Note: `{planner}` is not in the registry yet — pin written anyway.\n"
            "Add it via models.yaml or re-run setup with an API provider.",
            flush=True,
        )
    if executor not in reg.models:
        print(
            f"Note: `{executor}` is not in the registry yet — pin written anyway.",
            flush=True,
        )
    else:
        from kageha.chat.model_commands import _model_is_native_tool_caller

        if not _model_is_native_tool_caller(executor, reg):
            print(
                f"Warning: `{executor}` cannot run native tool loops "
                "(e.g. Antigravity/gemini-cli). "
                "`tool_calling` will keep an API-capable ladder.",
                flush=True,
            )

    return {
        "session_default": session_default,
        "planner": planner,
        "executor": executor,
    }


def _configure_packs(env_path: Path) -> tuple[list[str], str | None]:
    existing = [
        p.strip()
        for p in (read_env_value("KAGEHA_TOOL_PACKS", env_path) or "").split(",")
        if p.strip()
    ]
    print(
        "\nOptional capabilities (core tools always load).\n"
        "  Nano Banana image gen is core — needs GEMINI_API_KEY\n"
        "  browser  — interactive Playwright / Comet\n"
        "  media    — Fal video (+ optional Fal stills; needs FAL_KEY)\n"
        "  computer — macOS desktop automation\n"
        "Answers overwrite KAGEHA_TOOL_PACKS in .env.\n",
        flush=True,
    )
    packs: list[str] = []
    image_model: str | None = None
    if _yn("Enable browser pack?", "browser" in existing):
        packs.append("browser")
    if _yn("Enable media pack (Fal)?", "media" in existing):
        packs.append("media")
        fal = _prompt_secret("Fal API key (FAL_KEY or FAL_API_KEY)", "FAL_API_KEY")
        if fal:
            upsert_env_key("FAL_API_KEY", fal, env_path)
        aliases = ", ".join(sorted(FAL_IMAGE_MODELS))
        image_default = (
            read_env_value("KAGEHA_FAL_IMAGE_MODEL", env_path)
            or default_fal_image_model()
        )
        image_model = _prompt(
            f"Default Fal image model ({aliases})",
            image_default,
        ).strip() or image_default
        upsert_env_key("KAGEHA_FAL_IMAGE_MODEL", image_model, env_path)
    else:
        # Clear previous image default when media pack is off.
        upsert_env_key("KAGEHA_FAL_IMAGE_MODEL", "", env_path)
    if platform.system() == "Darwin":
        if _yn("Enable computer pack (macOS)?", "computer" in existing):
            packs.append("computer")
    else:
        print(
            f"(Skipping computer pack — this host is {platform.system()}, not macOS.)",
            flush=True,
        )
    # Always overwrite — empty string clears previous packs.
    upsert_env_key("KAGEHA_TOOL_PACKS", ",".join(packs), env_path)
    return packs, image_model


def configure_channels(env_path: Path | None = None) -> dict[str, bool]:
    """Persist guided Telegram/WhatsApp channel configuration."""
    env_path = env_path or (Path.cwd() / ".env")
    if not env_path.is_file():
        env_path.write_text("# Kageha env\n", encoding="utf-8")
    existing_telegram = bool(read_env_value("TELEGRAM_BOT_TOKEN", env_path))
    existing_whatsapp = bool(read_env_value("WHATSAPP_QR_ENABLED", env_path))
    print(
        "\nMessaging channels\n"
        "  Configure once here; `kageha webui` and `kageha chat` will start\n"
        "  configured channels automatically.\n",
        flush=True,
    )
    telegram = _yn("Enable Telegram?", existing_telegram)
    if telegram:
        token = _prompt_secret("Telegram bot token", "TELEGRAM_BOT_TOKEN")
        allowed = _prompt(
            "Allowed Telegram user IDs (comma-separated)",
            read_env_value("TELEGRAM_ALLOWED_USERS", env_path),
        )
        if not token or not allowed:
            raise ValueError("Telegram requires a bot token and at least one allowed user ID")
        upsert_env_key("TELEGRAM_BOT_TOKEN", token, env_path)
        upsert_env_key("TELEGRAM_ALLOWED_USERS", allowed, env_path)
    whatsapp = _yn("Enable WhatsApp QR?", existing_whatsapp)
    if whatsapp:
        allowed = _prompt(
            "Allowed WhatsApp numbers (international, no + or spaces)",
            read_env_value("WHATSAPP_QR_ALLOWED_USERS", env_path),
        )
        if not allowed:
            raise ValueError("WhatsApp requires at least one allowed phone number")
        upsert_env_key("WHATSAPP_QR_ENABLED", "1", env_path)
        upsert_env_key("WHATSAPP_QR_ALLOWED_USERS", allowed, env_path)
        upsert_env_key("WHATSAPP_QR_ALLOW_ALL_USERS", "", env_path)
    configured = telegram or whatsapp
    upsert_env_key("KAGEHA_CHANNEL_AUTOSTART", "1" if configured else "0", env_path)
    print(f"Saved channel configuration → {env_path}", flush=True)
    if whatsapp:
        print(
            "Start `kageha webui` to launch WhatsApp; scan the QR in the terminal on first use.",
            flush=True,
        )
    return {"telegram": telegram, "whatsapp": whatsapp}


def _print_next_steps(
    *,
    surface: Surface,
    workspace: Path,
    packs: list[str],
    model_id: str | None,
    planner: str | None = None,
    executor: str | None = None,
    image_model: str | None = None,
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
    configured = f" (default: `{model_id}`)" if model_id else ""
    planner_line = f"  /model planner {planner}\n" if planner else ""
    executor_line = f"  /model executor {executor}\n" if executor else ""
    print(
        "\nIn chat later:\n"
        f"  /model  switch models{configured}\n"
        f"{planner_line}{executor_line}"
        "  /plan   clarify → research → plan.md → /build\n"
        "  /goal   execute now with Approve / Deny / Suggest\n"
        "  /normal everyday chat",
        flush=True,
    )
    print(
        f"\nPacks in .env: KAGEHA_TOOL_PACKS={','.join(packs) or '(none)'}",
        flush=True,
    )
    if image_model:
        print(f"Image model: KAGEHA_FAL_IMAGE_MODEL={image_model}", flush=True)
    print(flush=True)


def run_setup(*, smoke_test: bool | None = None) -> dict[str, Any]:
    """Interactive guided setup. Re-run overwrites packs + role/image pins."""
    print(
        "\nKageha setup\n"
        "------------\n"
        "One wizard for surface, connection (API key or OAuth),\n"
        "planner/executor models, packs, and image model.\n"
        "Re-running overwrites those settings.\n",
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
            "Re-run `kageha setup` when ready.\n",
            flush=True,
        )
        return {"ok": False, **provider, "surface": surface, "workspace": str(workspace)}

    model_id = str(provider.get("model_id") or "")
    role_pins = _configure_role_models(model_id) if model_id else {
        "session_default": "",
        "planner": "",
        "executor": "",
    }
    packs, image_model = _configure_packs(env_path)

    yaml_path = provider.get("yaml_path") or str(kageha_home() / "models.yaml")
    if model_id:
        pinned = persist_setup_model_pins(
            session_default=role_pins["session_default"] or model_id,
            planner=role_pins["planner"] or model_id,
            executor=role_pins["executor"] or model_id,
        )
        yaml_path = str(pinned)
        print(
            f"\nPinned models → {yaml_path}\n"
            f"  session:  {role_pins['session_default'] or model_id}\n"
            f"  planner:  {role_pins['planner'] or model_id}\n"
            f"  executor: {role_pins['executor'] or model_id}",
            flush=True,
        )

    print(f"Saved .env → {env_path}", flush=True)
    if provider.get("api_key_env"):
        print(f"Saved model `{model_id}` → {yaml_path}\n", flush=True)
    else:
        print(f"OAuth model default `{model_id}`\n", flush=True)

    if smoke_test is None:
        smoke_test = _yn("Run a quick model smoke test now?", True)

    smoke_ok: bool | None = None
    smoke_error = ""
    if smoke_test and model_id:
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
        planner=role_pins.get("planner") or None,
        executor=role_pins.get("executor") or None,
        image_model=image_model,
    )
    return {
        "ok": True,
        "surface": surface,
        "workspace": str(workspace),
        "env_path": str(env_path),
        "yaml_path": yaml_path,
        "provider": provider.get("provider"),
        "model_id": model_id,
        "planner": role_pins.get("planner"),
        "executor": role_pins.get("executor"),
        "image_model": image_model,
        "packs": packs,
        "auth": provider.get("auth"),
        "smoke_ok": smoke_ok,
        "smoke_error": smoke_error,
    }
