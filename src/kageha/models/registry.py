"""Load models.yaml and construct chat models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from kageha.config import env_key, models_yaml_paths, project_root
from kageha.models.anthropic_compat import AnthropicCompatModel
from kageha.models.base import ChatModel
from kageha.models.gemini import GeminiModel
from kageha.models.openai_compat import OpenAICompatModel


@dataclass
class ProviderConfig:
    name: str
    protocol: str
    base_url: str
    api_key_env: str


@dataclass
class ModelConfig:
    id: str
    provider: str
    model: str
    roles: list[str] = field(default_factory=list)
    usd_per_1k: float | None = None
    usd_per_1k_input: float | None = None
    usd_per_1k_output: float | None = None
    # Optional prompt-cache rates (USD per 1k tokens). When unset, cache reads
    # default to ~0.1× input via KAGEHA_CACHE_READ_MULTIPLIER.
    usd_per_1k_cached_input: float | None = None
    usd_per_1k_cache_write: float | None = None
    capabilities: list[str] = field(default_factory=list)
    context_window: int = 0


@dataclass
class ModelRegistry:
    providers: dict[str, ProviderConfig]
    models: dict[str, ModelConfig]
    roles: dict[str, list[str]]
    embedding: dict[str, Any] = field(default_factory=dict)
    voice: dict[str, Any] = field(default_factory=dict)
    model_policy: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> ModelRegistry:
        data: dict[str, Any] = {}
        paths = [path] if path else models_yaml_paths()
        if not paths:
            # Fallback to repo default even if missing from home
            default = project_root() / "models.yaml"
            if default.is_file():
                paths = [default]
        for p in paths:
            if p and p.is_file():
                chunk = yaml.safe_load(p.read_text()) or {}
                data = _deep_merge(data, chunk)

        providers = {
            name: ProviderConfig(
                name=name,
                protocol=cfg["protocol"],
                base_url=cfg.get("base_url", ""),
                api_key_env=cfg["api_key_env"],
            )
            for name, cfg in (data.get("providers") or {}).items()
        }
        models = {}
        for m in data.get("models") or []:
            if not isinstance(m, dict) or "id" not in m:
                continue
            models[m["id"]] = ModelConfig(
                id=m["id"],
                provider=m["provider"],
                model=m["model"],
                roles=list(m.get("roles") or []),
                usd_per_1k=_optional_float(m.get("usd_per_1k")),
                usd_per_1k_input=_optional_float(
                    m.get("usd_per_1k_input") or m.get("input_usd_per_1k")
                ),
                usd_per_1k_output=_optional_float(
                    m.get("usd_per_1k_output") or m.get("output_usd_per_1k")
                ),
                usd_per_1k_cached_input=_optional_float(
                    m.get("usd_per_1k_cached_input")
                    or m.get("cached_input_usd_per_1k")
                ),
                usd_per_1k_cache_write=_optional_float(
                    m.get("usd_per_1k_cache_write")
                    or m.get("cache_write_usd_per_1k")
                ),
                capabilities=list(m.get("capabilities") or []),
                context_window=int(m.get("context_window") or 0),
            )
        roles = {k: list(v) for k, v in (data.get("roles") or {}).items()}
        # Derive role ladders from model.roles if not explicit
        for mid, mc in models.items():
            for role in mc.roles:
                roles.setdefault(role, [])
                if mid not in roles[role]:
                    roles[role].append(mid)
        policy = data.get("model_policy") or {}
        if not isinstance(policy, dict):
            policy = {}
        voice = data.get("voice") or {}
        if not isinstance(voice, dict):
            voice = {}
        return cls(
            providers=providers,
            models=models,
            roles=roles,
            embedding=dict(data.get("embedding") or {}),
            voice=dict(voice),
            model_policy=dict(policy),
        )

    def _provider_ready(
        self,
        pc: ProviderConfig,
        *,
        import_missing: bool = True,
    ) -> bool:
        """Whether this provider can serve chat (API key, Codex OAuth, or gemini CLI)."""
        if pc.protocol == "gemini_cli":
            from kageha.models.gemini_cli import (
                antigravity_session_present,
                gemini_cli_available,
            )

            return gemini_cli_available() and (
                antigravity_session_present() or bool(env_key(pc.api_key_env))
            )
        key, _ = self._resolve_credentials(pc, import_missing=import_missing)
        return bool(key)

    def _resolve_credentials(
        self,
        pc: ProviderConfig,
        *,
        import_missing: bool = True,
    ) -> tuple[str, dict[str, str]]:
        """Resolve credentials by auth path.

        - **API key providers** (gemini, openai, anthropic, …): env only.
        - **openai-codex**: env sentinel or ChatGPT/Codex OAuth store.
        - **gemini_cli / antigravity**: no token returned here (CLI session).
        """
        if pc.protocol == "gemini_cli":
            # Presence marker for callers; real auth is the gemini CLI session.
            return ("gemini-cli-session", {}) if self._provider_ready(
                pc, import_missing=False
            ) else ("", {})

        key = env_key(pc.api_key_env)
        if key:
            return key, {}

        # Public Gemini API must use GEMINI_API_KEY — do NOT treat Antigravity /
        # Gemini CLI OAuth tokens as API keys (wrong endpoint + account risk).
        if pc.protocol == "gemini" or pc.name in {"gemini", "google"}:
            return "", {}

        from kageha.models.auth_store import resolve_access_token

        # Codex / ChatGPT subscription path
        if pc.name in {"openai-codex", "chatgpt"} or pc.api_key_env.upper().startswith(
            "OPENAI_CODEX"
        ):
            token, headers = resolve_access_token(
                "openai-codex",
                import_missing=import_missing,
            )
            if token:
                return token, headers
        if pc.name == "openai" or pc.api_key_env == "OPENAI_API_KEY":
            # Optional: allow Codex OAuth to back the openai provider when no key
            token, headers = resolve_access_token(
                "chatgpt",
                import_missing=import_missing,
            )
            if token:
                return token, headers
        return "", {}

    def available_models(self) -> list[ModelConfig]:
        out = []
        for m in self.models.values():
            p = self.providers.get(m.provider)
            if not p:
                continue
            if self._provider_ready(p, import_missing=False):
                out.append(m)
        return out

    def auth_source(self, model_id: str) -> str:
        """Short label for /model list: api-key | codex | antigravity-cli | missing."""
        mc = self.models.get(model_id)
        if not mc:
            return "missing"
        pc = self.providers.get(mc.provider)
        if not pc:
            return "missing"
        if pc.protocol == "gemini_cli":
            return (
                "antigravity-cli"
                if self._provider_ready(pc, import_missing=False)
                else "missing"
            )
        if env_key(pc.api_key_env):
            return "api-key"
        if pc.name in {"openai-codex", "chatgpt"} or "CODEX" in pc.api_key_env.upper():
            return (
                "codex"
                if self._provider_ready(pc, import_missing=False)
                else "missing"
            )
        if pc.name == "openai" and not env_key("OPENAI_API_KEY"):
            return (
                "codex"
                if self._provider_ready(pc, import_missing=False)
                else "missing"
            )
        return (
            "api-key"
            if self._provider_ready(pc, import_missing=False)
            else "missing"
        )

    def build(self, model_id: str) -> ChatModel:
        mc = self.models.get(model_id)
        if not mc:
            raise KeyError(f"Unknown model id: {model_id}")
        pc = self.providers.get(mc.provider)
        if not pc:
            raise KeyError(f"Unknown provider: {mc.provider}")

        if pc.protocol == "gemini_cli":
            from kageha.models.gemini_cli import GeminiCliModel, gemini_cli_available

            if not gemini_cli_available():
                raise RuntimeError(
                    "Antigravity / Gemini CLI path needs the `gemini` binary on PATH.\n"
                    "Install Gemini CLI, sign in (or use Antigravity), then:\n"
                    "  /model antigravity\n"
                    "For full tool-calling agents prefer: GEMINI_API_KEY + /model gemini-flash"
                )
            return GeminiCliModel(
                model_id=mc.id,
                provider=pc.name,
                model=mc.model,
            )

        key, extra_headers = self._resolve_credentials(pc)
        if not key:
            if pc.protocol == "gemini":
                raise RuntimeError(
                    f"Missing {pc.api_key_env} for Gemini API models.\n"
                    "Set GEMINI_API_KEY (AI Studio), or use Antigravity via:\n"
                    "  /model antigravity\n"
                    "Codex subscription: /model gpt-codex (after codex login)"
                )
            raise RuntimeError(
                f"Missing credentials for {mc.id}.\n"
                "Paths:\n"
                "  • API key: set OPENAI_API_KEY / GEMINI_API_KEY / …\n"
                "  • Codex:   codex login → /model gpt-codex\n"
                "  • Antigravity: gemini CLI signed in → /model antigravity"
            )
        if pc.protocol == "openai_compat":
            return OpenAICompatModel(
                model_id=mc.id,
                provider=pc.name,
                model=mc.model,
                base_url=pc.base_url,
                api_key=key,
                extra_headers=extra_headers,
            )
        if pc.protocol == "anthropic_compat":
            return AnthropicCompatModel(
                model_id=mc.id,
                provider=pc.name,
                model=mc.model,
                base_url=pc.base_url or "https://api.anthropic.com",
                api_key=key,
            )
        if pc.protocol == "gemini":
            return GeminiModel(
                model_id=mc.id,
                provider=pc.name,
                model=mc.model,
                api_key=key,
                base_url=pc.base_url or "https://generativelanguage.googleapis.com/v1beta",
            )
        raise ValueError(f"Unsupported protocol: {pc.protocol}")

    def add_model(
        self,
        *,
        model_id: str,
        protocol: str,
        base_url: str,
        api_key_env: str,
        model: str,
        roles: list[str],
        provider_name: str | None = None,
        path: Path | None = None,
    ) -> Path:
        """Persist a new model entry to ~/.kageha/models.yaml (or path)."""
        from kageha.config import kageha_home

        target = path or (kageha_home() / "models.yaml")
        data: dict[str, Any] = {}
        if target.is_file():
            data = yaml.safe_load(target.read_text()) or {}
        pname = provider_name or f"custom_{protocol}"
        providers = data.setdefault("providers", {})
        providers[pname] = {
            "protocol": protocol,
            "base_url": base_url,
            "api_key_env": api_key_env,
        }
        models = data.setdefault("models", [])
        models = [m for m in models if m.get("id") != model_id]
        models.append(
            {
                "id": model_id,
                "provider": pname,
                "model": model,
                "roles": roles,
            }
        )
        data["models"] = models
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(data, sort_keys=False))
        return target


def _optional_float(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def estimate_model_usd(
    model: ModelConfig | None,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
    default_per_1k: float | None = None,
) -> float:
    """Estimate USD for a completion using per-model rates.

    Prompt-cache hits are credited when ``cached_tokens`` is set. By default
    cached input is billed at ``KAGEHA_CACHE_READ_MULTIPLIER`` (0.1) × the
    input rate (Anthropic-style). Prefer ``ModelConfig.usd_per_1k_cached_input``
    / ``usd_per_1k_cache_write`` when present in models.yaml.

    ``prompt_tokens`` is treated as total input. When ``cached_tokens`` ≤
    prompt, cached is a subset (OpenAI / Gemini / normalized Anthropic). When
    cached exceeds prompt (raw Anthropic-style additive reporting), prompt is
    treated as uncached-only and cache reads are billed on top.
    """
    import os

    if default_per_1k is None:
        try:
            default_per_1k = float(
                os.environ.get("KAGEHA_USD_PER_1K_DEFAULT", "0.001") or "0.001"
            )
        except ValueError:
            default_per_1k = 0.001
    try:
        cache_mult = float(
            os.environ.get("KAGEHA_CACHE_READ_MULTIPLIER", "0.1") or "0.1"
        )
    except ValueError:
        cache_mult = 0.1

    prompt = max(0, int(prompt_tokens or 0))
    cached = max(0, int(cached_tokens or 0))
    cache_write = max(0, int(cache_write_tokens or 0))
    completion = max(0, int(completion_tokens or 0))

    # Inclusive (cached ⊆ prompt) vs additive (Anthropic raw input_tokens).
    if cached > prompt:
        uncached = prompt
    else:
        uncached = prompt - cached

    pin_uncached = uncached / 1000.0
    pin_cached = cached / 1000.0
    pin_write = cache_write / 1000.0
    cout = completion / 1000.0

    if model is None:
        rin = rout = default_per_1k
        r_cached = rin * cache_mult
        r_write = None
    elif model.usd_per_1k_input is not None or model.usd_per_1k_output is not None:
        rin = (
            model.usd_per_1k_input
            if model.usd_per_1k_input is not None
            else (model.usd_per_1k if model.usd_per_1k is not None else default_per_1k)
        )
        rout = (
            model.usd_per_1k_output
            if model.usd_per_1k_output is not None
            else (model.usd_per_1k if model.usd_per_1k is not None else default_per_1k)
        )
        r_cached = (
            model.usd_per_1k_cached_input
            if model.usd_per_1k_cached_input is not None
            else rin * cache_mult
        )
        r_write = model.usd_per_1k_cache_write
    elif model.usd_per_1k is not None:
        rin = rout = model.usd_per_1k
        r_cached = (
            model.usd_per_1k_cached_input
            if model.usd_per_1k_cached_input is not None
            else rin * cache_mult
        )
        r_write = model.usd_per_1k_cache_write
    else:
        rin = rout = default_per_1k
        r_cached = (
            model.usd_per_1k_cached_input
            if model.usd_per_1k_cached_input is not None
            else rin * cache_mult
        )
        r_write = model.usd_per_1k_cache_write

    total = pin_uncached * rin + pin_cached * r_cached + cout * rout
    if r_write is not None and pin_write:
        total += pin_write * r_write
    return total


def _deep_merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        elif k == "models" and isinstance(v, list):
            # Later files override by id
            by_id = {m["id"]: m for m in out.get("models", []) if isinstance(m, dict)}
            for m in v:
                if isinstance(m, dict) and "id" in m:
                    by_id[m["id"]] = m
            out["models"] = list(by_id.values())
        else:
            out[k] = v
    return out
