"""Role-based model router with fallback ladder and anti-retry ledger."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from kageha.models.base import ChatMessage, ChatModel, ChatResponse, ToolSpec
from kageha.models.registry import ModelRegistry


@dataclass
class RouteAttempt:
    role: str
    requested: str
    actual: str
    reason: str
    error: str | None = None


def sanitize_messages_for_provider(
    messages: list[ChatMessage],
    *,
    target_provider: str,
    source_provider: str | None,
    force: bool = False,
) -> list[ChatMessage]:
    """Collapse tool-call turns when switching providers mid-run.

    Gemini often returns HTTP 400 if handed OpenAI/Kimi-style tool history
    (or missing thought signatures). Flattening keeps the facts, drops schema.
    """
    if not force and (not source_provider or source_provider == target_provider):
        return messages
    out: list[ChatMessage] = []
    for m in messages:
        if m.role == "tool":
            out.append(
                ChatMessage(
                    role="user",
                    content=f"[tool:{m.name or 'tool'} result]\n{(m.content or '')[:4000]}",
                )
            )
            continue
        if m.role == "assistant" and m.tool_calls:
            # Never emit assistant stubs like "[called tools: …]" — models echo
            # them as the final answer. Keep only a user-side breadcrumb + results.
            names = ", ".join(tc.name for tc in m.tool_calls[:8])
            text = (m.content or "").strip()
            crumb = f"[prior step called {names}]"
            if text:
                crumb = f"{text[:500]}\n{crumb}"
            out.append(ChatMessage(role="user", content=crumb[:2000]))
            continue
        out.append(m)
    return out


def _short_err(exc: BaseException, *, limit: int = 240) -> str:
    text = str(exc).replace("\n", " ").strip()
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text or exc.__class__.__name__


@dataclass
class ModelRouter:
    registry: ModelRegistry
    circuit_failures: dict[str, int] = field(default_factory=dict)
    anti_retry: set[tuple[str, str, str]] = field(default_factory=set)
    history: list[RouteAttempt] = field(default_factory=list)
    sticky: dict[str, str] = field(default_factory=dict)  # task_id -> model_id
    last_provider: dict[str, str] = field(default_factory=dict)  # task_id -> provider
    last_model: dict[str, str] = field(default_factory=dict)  # task_id -> model_id
    # Session-scoped pin: preferred for every role until cleared or it fails hard.
    session_override: str | None = None
    # Per router-role pins (e.g. planning → antigravity). Wins over session_override.
    role_overrides: dict[str, str] = field(default_factory=dict)
    # One-shot pin: preferred until the next successful chat(), then cleared.
    once_override: str | None = None
    # Optional callback when once_override is consumed (e.g. clear session.json).
    on_once_consumed: Any | None = None
    # Optional callback(from_id, to_id, error) when a chat() call recovers via ladder.
    on_failover: Any | None = None
    open_circuit_after: int = 3
    provider_control: Any | None = None
    # Structured notices for UI (chat/verbose); drained by consumers.
    failover_notices: list[dict[str, Any]] = field(default_factory=list)

    def ladder(self, role: str) -> list[str]:
        return list(self.registry.roles.get(role) or self.registry.roles.get("default") or [])

    def set_session_override(self, model_id: str | None) -> None:
        self.session_override = model_id or None

    def set_role_overrides(self, overrides: dict[str, str] | None) -> None:
        clean: dict[str, str] = {}
        for role, mid in (overrides or {}).items():
            r = str(role).strip()
            m = str(mid).strip() if mid is not None else ""
            if r and m:
                clean[r] = m
        self.role_overrides = clean

    def set_once_override(self, model_id: str | None) -> None:
        self.once_override = model_id or None

    def pick(self, role: str = "default", *, task_id: str = "") -> ChatModel:
        model = self._pick_untried(role, task_id=task_id, tried=set())
        return model

    def record_failure(
        self,
        model_id: str,
        *,
        task_id: str = "",
        failure_class: str = "hard_fail",
        error: str = "",
    ) -> None:
        # Auth / quota trip hard; empty/timeout are soft (don't burn the ladder).
        if failure_class == "transient":
            bump = 0
        elif failure_class in {"auth", "quota"}:
            bump = 3
        else:
            bump = 1
        self.circuit_failures[model_id] = self.circuit_failures.get(model_id, 0) + bump
        self.anti_retry.add((task_id, model_id, failure_class))
        # Break sticky if this model keeps failing
        if self.sticky.get(task_id) == model_id and self.circuit_failures[model_id] >= 2:
            self.sticky.pop(task_id, None)
        # Soft-drop session pin after repeated hard failures so ladder can recover.
        if (
            self.session_override == model_id
            and self.circuit_failures[model_id] >= self.open_circuit_after
        ):
            self.session_override = None
        # Soft-drop any role pins for this model too.
        if self.circuit_failures[model_id] >= self.open_circuit_after:
            self.role_overrides = {
                r: m for r, m in self.role_overrides.items() if m != model_id
            }
        control = self.provider_control
        if control is not None:
            model = self.registry.models.get(model_id)
            control.record_route_failure(
                model_id=model_id,
                provider=model.provider if model else "",
                error=error or failure_class,
                failure_class=failure_class,
            )

    def record_success(self, model_id: str, *, task_id: str = "", provider: str = "") -> None:
        self.circuit_failures[model_id] = 0
        if task_id:
            self.sticky[task_id] = model_id
            self.last_model[task_id] = model_id
            if provider:
                self.last_provider[task_id] = provider
        if self.once_override:
            self.once_override = None
            cb = self.on_once_consumed
            if callable(cb):
                try:
                    cb(model_id)
                except Exception:  # noqa: BLE001
                    pass
        control = self.provider_control
        if control is not None:
            control.record_route_success(
                model_id=model_id,
                provider=provider,
            )

    def drain_failover_notices(self) -> list[dict[str, Any]]:
        """Return and clear pending failover UX notices."""
        out = list(self.failover_notices)
        self.failover_notices.clear()
        return out

    @staticmethod
    def format_failover_line(notice: dict[str, Any]) -> str:
        """One-line Hermes-style failover summary."""
        frm = str(notice.get("from") or "?")
        to = str(notice.get("to") or "?")
        err = str(notice.get("error") or "error").strip()
        if len(err) > 80:
            err = err[:79] + "…"
        role = str(notice.get("role") or "")
        role_bit = f" role={role}" if role else ""
        return f"model: {frm} → {to} ({err}){role_bit}"

    def _record_failover(
        self,
        *,
        role: str,
        from_id: str,
        to_id: str,
        error: str,
    ) -> None:
        notice = {
            "from": from_id,
            "to": to_id,
            "error": error,
            "role": role,
        }
        self.failover_notices.append(notice)
        self.history.append(
            RouteAttempt(
                role=role,
                requested=from_id,
                actual=to_id,
                reason="recovered",
                error=error,
            )
        )
        cb = self.on_failover
        if callable(cb):
            try:
                cb(from_id, to_id, error)
            except Exception:  # noqa: BLE001
                pass

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        *,
        role: str = "default",
        task_id: str = "",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        effort: str | None = None,
        exclude_providers: set[str] | None = None,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> tuple[ChatModel, ChatResponse]:
        from kageha.models.streaming import collect_stream, supports_stream

        attempt_errors: list[str] = []
        tried: set[str] = set()
        failed_ids: list[str] = []
        last_err = ""
        ladder = self.ladder(role)
        # Native tool loops need API/Codex providers — Antigravity/gemini-cli is
        # text-only and will invent "tools missing" excuses if selected.
        require_tool_calling = bool(tools)
        # One pass per ladder entry (plus sticky bump handled inside pick).
        for _ in range(max(1, len(ladder) + 1)):
            try:
                model = self._pick_untried(
                    role,
                    task_id=task_id,
                    tried=tried,
                    exclude_providers=exclude_providers or set(),
                    require_tool_calling=require_tool_calling,
                )
            except RuntimeError:
                break
            tried.add(model.model_id)
            prev_provider = self.last_provider.get(task_id)
            prev_model = self.last_model.get(task_id)
            use_messages = sanitize_messages_for_provider(
                messages,
                target_provider=model.provider,
                source_provider=prev_provider,
                # Gemini tool-call signatures can be model-specific. Normalize
                # history on every fallback model switch, even within a provider.
                force=bool(prev_model and prev_model != model.model_id),
            )
            try:
                resp: ChatResponse | None = None
                if on_text_delta is not None and supports_stream(model):
                    try:
                        resp = await collect_stream(
                            model.stream(
                                use_messages,
                                tools,
                                temperature=temperature,
                                max_tokens=max_tokens,
                                effort=effort,
                            ),
                            on_text_delta=on_text_delta,
                            model_id=model.model_id,
                        )
                    except Exception as stream_exc:  # noqa: BLE001
                        # Fall back to buffered chat on this model before ladder.
                        last_err = _short_err(stream_exc)
                        resp = None
                    else:
                        # Some providers (e.g. Z.AI GLM reasoning streams) can
                        # finish a stream with neither visible text nor tool
                        # calls. Treat that as a soft stream miss and retry
                        # buffered chat on the same model before failing over.
                        if (
                            resp is not None
                            and not (resp.message.content or "").strip()
                            and not resp.message.tool_calls
                        ):
                            last_err = "empty stream response"
                            resp = None
                if resp is None:
                    resp = await model.chat(
                        use_messages,
                        tools,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        effort=effort,
                    )
                # Empty text with no tools is a soft failure — try next model.
                if not (resp.message.content or "").strip() and not resp.message.tool_calls:
                    raise RuntimeError("empty model response")
                self.record_success(
                    model.model_id, task_id=task_id, provider=model.provider
                )
                if failed_ids:
                    self._record_failover(
                        role=role,
                        from_id=failed_ids[0],
                        to_id=model.model_id,
                        error=last_err or "ladder failover",
                    )
                return model, resp
            except Exception as e:  # noqa: BLE001
                err = _short_err(e)
                last_err = err
                failed_ids.append(model.model_id)
                attempt_errors.append(f"{model.model_id}: {err}")
                failure_class = _classify_route_failure(err)
                self.record_failure(
                    model.model_id,
                    task_id=task_id,
                    failure_class=failure_class,
                    error=err,
                )
                self.history.append(
                    RouteAttempt(
                        role=role,
                        requested=model.model_id,
                        actual=model.model_id,
                        reason="fallback",
                        error=err,
                    )
                )
        detail = "; ".join(attempt_errors) if attempt_errors else "no eligible models"
        raise RuntimeError(f"All models failed for role={role}. {detail}")

    def _model_supports_tool_calling(self, model_id: str) -> bool:
        configured = self.registry.models.get(model_id)
        if configured is None:
            return False
        if "tool_calling" not in set(configured.capabilities or []):
            return False
        provider = self.registry.providers.get(configured.provider)
        # gemini_cli / Antigravity is prompt-only; never treat as native tools.
        if provider is not None and provider.protocol == "gemini_cli":
            return False
        return True

    def _pick_untried(
        self,
        role: str,
        *,
        task_id: str,
        tried: set[str],
        exclude_providers: set[str] | None = None,
        require_tool_calling: bool = False,
    ) -> ChatModel:
        """Pick a model not yet attempted in this individual chat request."""
        ladder = self.ladder(role)
        once = self.once_override
        role_pin = self.role_overrides.get(role)
        override = self.session_override
        sticky_id = self.sticky.get(task_id)
        ordered: list[str] = []
        # once > per-role pin > all-roles session pin > sticky > ladder
        for mid in (once, role_pin, override, sticky_id, *ladder):
            if mid and mid not in ordered:
                ordered.append(mid)
        available_ids = {m.id for m in self.registry.available_models()}
        errors: list[str] = []
        skipped_for_tools: list[str] = []
        for mid in ordered:
            if not mid:
                continue
            if mid in tried:
                errors.append(f"{mid}:already_tried")
                continue
            if mid not in available_ids:
                errors.append(f"{mid}:key_missing")
                continue
            configured = self.registry.models.get(mid)
            if configured and configured.provider in (exclude_providers or set()):
                errors.append(f"{mid}:provider_excluded")
                continue
            if require_tool_calling and not self._model_supports_tool_calling(mid):
                errors.append(f"{mid}:no_tool_calling")
                skipped_for_tools.append(mid)
                continue
            if self.circuit_failures.get(mid, 0) >= self.open_circuit_after:
                errors.append(f"{mid}:circuit_open")
                continue
            if self.provider_control is not None and not self.provider_control.is_model_healthy(mid):
                errors.append(f"{mid}:persistent_circuit_open")
                continue
            try:
                model = self.registry.build(mid)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{mid}:{e}")
                continue
            if mid == once:
                reason = "once_override"
            elif mid == role_pin:
                reason = "role_override"
            elif mid == override:
                reason = "session_override"
            elif mid == sticky_id:
                reason = "sticky"
            else:
                reason = "selected"
            if skipped_for_tools and mid != skipped_for_tools[0]:
                # Surface the silent Antigravity → API hop in UI/history.
                self._record_failover(
                    role=role,
                    from_id=skipped_for_tools[0],
                    to_id=mid,
                    error="no_tool_calling (use API model for native tools)",
                )
            self.history.append(
                RouteAttempt(
                    role=role,
                    requested=ladder[0] if ladder else mid,
                    actual=mid,
                    reason=reason,
                )
            )
            return model
        hint = ""
        if require_tool_calling and skipped_for_tools:
            hint = (
                " Native tool loops need GEMINI_API_KEY + /model gemini-flash "
                "(or gemini-pro / glm-5.2). Antigravity CLI cannot call computer_*."
            )
        raise RuntimeError(
            f"No model available for role={role}. Tried: {', '.join(errors) or 'none'}.{hint}"
        )

    def stats(self) -> dict[str, Any]:
        return {
            "circuit_failures": dict(self.circuit_failures),
            "anti_retry": [list(x) for x in self.anti_retry],
            "sticky": dict(self.sticky),
            "last_model": dict(self.last_model),
            "session_override": self.session_override,
            "role_overrides": dict(self.role_overrides),
            "once_override": self.once_override,
            "history": [
                {
                    "role": h.role,
                    "requested": h.requested,
                    "actual": h.actual,
                    "reason": h.reason,
                    "error": h.error,
                }
                for h in self.history
            ],
        }


def _classify_route_failure(err: str) -> str:
    e = (err or "").lower()
    if "403" in e or "401" in e or "forbidden" in e or "unauthorized" in e:
        return "auth"
    if "429" in e or "rate limit" in e or "quota" in e:
        return "quota"
    # Soft blips — try the next ladder model without opening a long circuit.
    if (
        "empty model response" in e
        or "timed out" in e
        or "timeout" in e
        or "function call turn" in e
        or "thoughtsignature" in e
        or "thought signature" in e
    ):
        return "transient"
    return "hard_fail"
