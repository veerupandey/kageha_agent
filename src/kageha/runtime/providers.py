"""Persistent provider health, capability routing and circuit-breaker policy."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any

from kageha.harness.circuit import circuit_allows
from kageha.models.registry import ModelRegistry
from kageha.runtime.store import RuntimeStore
from kageha.runtime.types import FailureClass, ProviderHealth


_AUTH_RE = re.compile(r"\b(401|403|unauthori[sz]ed|invalid api key|authentication)\b", re.I)
_HARD_QUOTA_RE = re.compile(
    r"\b(402|insufficient[_\s-]?quota|billing|payment.?required|credit)\b", re.I
)
_RATE_LIMIT_RE = re.compile(r"\b(429|rate.?limit|too many requests|tpm|rpm)\b", re.I)
_TIMEOUT_RE = re.compile(r"\b(timeout|timed out|deadline exceeded)\b", re.I)
_TRANSIENT_RE = re.compile(r"\b(408|409|425|500|502|503|504|connection|temporar)\b", re.I)
_MODEL_TURN_RE = re.compile(r"ending with a model turn", re.I)


def classify_provider_failure(error: str) -> FailureClass:
    text = error or ""
    if _AUTH_RE.search(text):
        return FailureClass.AUTH
    if _HARD_QUOTA_RE.search(text):
        return FailureClass.QUOTA
    if _RATE_LIMIT_RE.search(text):
        return FailureClass.RATE_LIMIT
    if _TIMEOUT_RE.search(text):
        return FailureClass.TIMEOUT
    if "empty model response" in text.lower() or "empty stream response" in text.lower():
        return FailureClass.TRANSIENT
    if _MODEL_TURN_RE.search(text):
        return FailureClass.TRANSIENT
    if _TRANSIENT_RE.search(text):
        return FailureClass.TRANSIENT
    return FailureClass.PROVIDER


@dataclass(frozen=True)
class ProviderRequirement:
    tool_calling: bool = False
    vision: bool = False
    structured_output: bool = False
    minimum_context: int = 0


class ProviderControlPlane:
    """Shares health and circuit state across turns and processes.

    Uses the same half-open rule as ``kageha.harness.circuit.CircuitBreaker``.
    """

    def __init__(
        self,
        store: RuntimeStore,
        registry: ModelRegistry | None = None,
        *,
        auto_heal: bool = True,
    ) -> None:
        self.store = store
        self.registry = registry or ModelRegistry.load()
        if auto_heal:
            # Drop expired durable open rows so status/report match eligibility.
            self.heal_circuits()

    def is_model_healthy(self, model_id: str) -> bool:
        """Return whether *model_id* may be selected for a new attempt."""
        for row in self.store.provider_health():
            if row["model_id"] != model_id:
                continue
            state = str(row.get("state") or "")
            if state == "key_missing":
                return False
            return circuit_allows(open_until=row.get("circuit_open_until"))
        return True

    def heal_circuits(self, *, force: bool = False) -> int:
        """Clear expired (or all, if *force*) open circuits so models can retry.

        Returns the number of health rows updated.
        """
        now = time.time()
        healed = 0
        for row in self.store.provider_health():
            state = str(row.get("state") or "")
            if state == "key_missing":
                continue
            open_until = row.get("circuit_open_until")
            cooling = open_until is not None and not circuit_allows(
                open_until=open_until, now=now
            )
            if cooling and not force:
                continue
            already_clean = (
                bool(row.get("available"))
                and state in {"closed", "configured", "unknown"}
                and open_until is None
            )
            if already_clean:
                continue
            dirty = state in {"open", "degraded"} or open_until is not None or (
                not bool(row.get("available")) and state not in {"missing"}
            )
            if not dirty and not force:
                continue
            config = self.registry.models.get(str(row["model_id"]))
            self.store.record_provider_health(
                ProviderHealth(
                    provider=str(row["provider"]),
                    model_id=str(row["model_id"]),
                    available=True,
                    state="unknown",
                    capabilities=list(
                        config.capabilities if config else (row.get("capabilities") or [])
                    ),
                    circuit_open_until=None,
                    error="",
                )
            )
            healed += 1
        return healed

    def supports(
        self,
        model_id: str,
        requirement: ProviderRequirement,
    ) -> bool:
        model = self.registry.models.get(model_id)
        if model is None:
            return False
        capabilities = set(model.capabilities)
        if requirement.tool_calling and "tool_calling" not in capabilities:
            return False
        if requirement.vision and "vision" not in capabilities:
            return False
        if requirement.structured_output and "structured_output" not in capabilities:
            return False
        return model.context_window >= requirement.minimum_context

    def record_route_success(
        self,
        *,
        model_id: str,
        provider: str,
        latency_ms: float = 0.0,
    ) -> None:
        config = self.registry.models.get(model_id)
        self.store.record_provider_health(
            ProviderHealth(
                provider=provider,
                model_id=model_id,
                available=True,
                state="closed",
                latency_ms=latency_ms,
                capabilities=list(config.capabilities if config else []),
            )
        )

    def record_route_failure(
        self,
        *,
        model_id: str,
        provider: str,
        error: str,
        failure_class: str = "",
    ) -> None:
        failure = (
            FailureClass(failure_class)
            if failure_class in {item.value for item in FailureClass}
            else classify_provider_failure(error)
        )
        cooldown = {
            FailureClass.AUTH: 900.0,
            FailureClass.QUOTA: 300.0,
            # Burst 429s: short cool-down so controller safety-net retry can run.
            FailureClass.RATE_LIMIT: 2.0,
            FailureClass.TIMEOUT: 30.0,
            # Empty responses / blips — keep short so the next chat turn can run.
            FailureClass.TRANSIENT: 5.0,
        }.get(failure, 60.0)
        # Prefer provider-supplied Retry-After when present.
        from kageha.models.retry import extract_retry_after

        retry_after = extract_retry_after(error)
        if retry_after is not None and failure in {
            FailureClass.RATE_LIMIT,
            FailureClass.TRANSIENT,
            FailureClass.TIMEOUT,
        }:
            # Cap so a large Retry-After cannot block the next attempt for minutes.
            cooldown = min(max(float(retry_after), 1.0), 8.0 if failure == FailureClass.RATE_LIMIT else 30.0)
        config = self.registry.models.get(model_id)
        self.store.record_provider_health(
            ProviderHealth(
                provider=provider,
                model_id=model_id,
                available=False,
                state="open",
                failure_class=failure,
                error=error[:1000],
                capabilities=list(config.capabilities if config else []),
                circuit_open_until=time.time() + cooldown,
            )
        )

    async def check_all(
        self,
        *,
        deep: bool = False,
        required: tuple[str, ...] = ("gemini", "openai", "siliconflow"),
    ) -> list[ProviderHealth]:
        """Check one configured model for every required independent provider."""
        checks = [
            self._check_provider(provider, deep=deep)
            for provider in required
        ]
        results = await asyncio.gather(*checks)
        for health in results:
            self.store.record_provider_health(health)
        return list(results)

    async def _check_provider(self, provider: str, *, deep: bool) -> ProviderHealth:
        models = [
            model
            for model in self.registry.models.values()
            if model.provider == provider
        ]
        if not models:
            return ProviderHealth(
                provider=provider,
                model_id="",
                available=False,
                state="missing",
                failure_class=FailureClass.PERMANENT,
                error="provider has no configured model",
            )
        config = models[0]
        provider_config = self.registry.providers.get(provider)
        if provider_config is None:
            return ProviderHealth(
                provider=provider,
                model_id=config.id,
                available=False,
                state="missing",
                failure_class=FailureClass.PERMANENT,
                error="provider configuration is missing",
            )
        from kageha.config import env_key

        if not env_key(provider_config.api_key_env):
            return ProviderHealth(
                provider=provider,
                model_id=config.id,
                available=False,
                state="key_missing",
                failure_class=FailureClass.AUTH,
                error=f"{provider_config.api_key_env} is not configured",
                capabilities=list(config.capabilities),
            )
        if not deep:
            return ProviderHealth(
                provider=provider,
                model_id=config.id,
                available=True,
                state="configured",
                capabilities=list(config.capabilities),
            )
        started = time.perf_counter()
        try:
            model = self.registry.build(config.id)
            reply = await asyncio.wait_for(model.smoke(), timeout=20.0)
            latency_ms = (time.perf_counter() - started) * 1000.0
            if not reply:
                raise RuntimeError("empty smoke response")
            return ProviderHealth(
                provider=provider,
                model_id=config.id,
                available=True,
                state="closed",
                latency_ms=latency_ms,
                capabilities=list(config.capabilities),
            )
        except Exception as exc:  # noqa: BLE001
            failure = classify_provider_failure(str(exc))
            return ProviderHealth(
                provider=provider,
                model_id=config.id,
                available=False,
                state="open",
                latency_ms=(time.perf_counter() - started) * 1000.0,
                failure_class=failure,
                error=str(exc)[:1000],
                capabilities=list(config.capabilities),
                circuit_open_until=time.time() + 60.0,
            )

    def report(self) -> dict[str, Any]:
        self.heal_circuits()
        rows = self.store.provider_health()
        required = {"gemini", "openai", "siliconflow"}
        healthy = {
            row["provider"]
            for row in rows
            if row["provider"] in required
            and self.is_model_healthy(str(row["model_id"]))
        }
        return {
            "required": sorted(required),
            "healthy": sorted(healthy),
            "ready": healthy == required,
            "providers": rows,
        }

