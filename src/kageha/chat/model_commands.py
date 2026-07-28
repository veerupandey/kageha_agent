"""Session model switching: /model list|reset|<id>|planner|executor …"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from kageha.config import kageha_home
from kageha.harness.sandbox import SessionWorkspace
from kageha.models.registry import ModelRegistry

# Logical chat slots → ModelRouter role names.
SLOT_TO_ROLES: dict[str, tuple[str, ...]] = {
    "planner": ("planning", "default"),
    "executor": ("tool_calling", "fast_worker", "monitor", "coding"),
}

SLOT_ALIASES: dict[str, str] = {
    "planner": "planner",
    "plan": "planner",
    "planning": "planner",
    "executor": "executor",
    "exec": "executor",
    "worker": "executor",
    "execution": "executor",
}


@dataclass
class ModelCommandResult:
    handled: bool
    override: str | None = None
    once: str | None = None
    role_overrides: dict[str, str] = field(default_factory=dict)
    message: str = ""
    # When True, pins were intentionally changed (incl. reset).
    changed: bool = False


def normalize_model_token(token: str) -> str:
    """Normalize natural phrases: 'gemini 3.1 pro' → 'gemini-3.1-pro'."""
    s = (token or "").strip().lower()
    s = s.replace("_", "-")
    s = re.sub(r"[\s/]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")


def resolve_slot(token: str) -> str | None:
    return SLOT_ALIASES.get((token or "").strip().lower())


def expand_role_overrides(slots: dict[str, str] | None) -> dict[str, str]:
    """Map planner/executor slots → concrete router role pins."""
    out: dict[str, str] = {}
    for slot, mid in (slots or {}).items():
        key = str(slot).strip().lower()
        model = str(mid).strip() if mid else ""
        if not model:
            continue
        for role in SLOT_TO_ROLES.get(key, ()):
            out[role] = model
    return out


# Friendly names → canonical models.yaml ids (duplicates collapsed on purpose).
_MODEL_ALIASES: dict[str, str] = {
    "agy": "antigravity",
    "antigravity-pro": "antigravity",
    "antigravity-3.1-pro": "antigravity",
    "3.1-pro": "antigravity",
    "3.1-pro-preview": "antigravity",
    "antigravity-3.6-flash": "antigravity-flash",
    "3.6-flash": "antigravity-flash",
    "3-flash": "antigravity-3-flash",
    "3-pro": "antigravity-3-pro",
    "2.5-flash": "antigravity-2.5-flash",
    "2.5-pro": "antigravity-2.5-pro",
    "codex": "gpt-codex",
    "sol": "gpt-codex",
    "gpt-5.6-sol": "gpt-codex",
    "gemini-3.6-flash": "gemini-flash",
    "gemini-3.1-pro": "gemini-pro",
    "glm": "glm-5.2",
    "glm5.2": "glm-5.2",
    "glm-5": "glm-5.2",
    "zai-org/glm-5.2": "glm-5.2",
}


def resolve_model_id(token: str, registry: ModelRegistry | None = None) -> str | None:
    """Resolve a user token to a configured model id (exact, alias, or fuzzy)."""
    reg = registry or ModelRegistry.load()
    raw = (token or "").strip()
    if not raw:
        return None
    needle = normalize_model_token(raw)
    if needle.startswith("agy-"):
        needle = "antigravity-" + needle[4:]
    ids = list(reg.models.keys())
    available = {m.id for m in reg.available_models()}
    id_lower = {i.lower(): i for i in ids}

    if needle in id_lower:
        return id_lower[needle]

    alias = _MODEL_ALIASES.get(needle)
    if alias and alias in id_lower:
        return id_lower[alias]

    wants_agy = needle.startswith("antigravity")
    # Natural phrases already normalized to kebab (e.g. gpt-5.5, antigravity-flash).
    for pat, mid in (
        (r"^gpt[- ]?5\.6[- ]?terra$", "gpt-5.6-terra"),
        (r"^gpt[- ]?5\.6[- ]?luna$", "gpt-5.6-luna"),
        (r"^gpt[- ]?5\.5$", "gpt-5.5"),
        (r"^gpt[- ]?5\.4[- ]?mini$", "gpt-5.4-mini"),
        (r"^gpt[- ]?5\.4$", "gpt-5.4"),
        (r"^gpt[- ]?4\.1$", "gpt-4.1"),
        (r"^gpt[- ]?fast$", "gpt-fast"),
        (r"^gemini[- ]?flash$", "gemini-flash"),
        (r"^gemini[- ]?pro$", "gemini-pro"),
        (r"^antigravity[- ]?flash$", "antigravity-flash"),
        (r"^antigravity[- ]?pro$", "antigravity"),
    ):
        if re.match(pat, needle) and mid in id_lower:
            return id_lower[mid]

    ordered = [m for m in ids if m in available] + [m for m in ids if m not in available]
    prefix = [m for m in ordered if m.lower().startswith(needle)]
    if len(prefix) == 1:
        return prefix[0]

    contains = [m for m in ordered if needle in m.lower()]
    if len(contains) == 1:
        return contains[0]
    if len(contains) > 1:
        pref = [
            m
            for m in contains
            if reg.models[m].provider == ("antigravity" if wants_agy else "gemini")
        ]
        if len(pref) == 1:
            return pref[0]

    by_api = [
        m
        for m in ordered
        if needle in (reg.models[m].model or "").lower()
        or (reg.models[m].model or "").lower().startswith(needle)
    ]
    if wants_agy:
        by_api = [m for m in by_api if reg.models[m].provider == "antigravity"]
    if len(by_api) == 1:
        return by_api[0]
    if len(by_api) > 1 and not wants_agy:
        api_only = [m for m in by_api if reg.models[m].provider == "gemini"]
        if len(api_only) == 1:
            return api_only[0]
    return None


def model_policy_allow(registry: ModelRegistry | None = None) -> list[str] | None:
    """Return allowlist from models.yaml ``model_policy.allow`` if non-empty."""
    reg = registry or ModelRegistry.load()
    policy = getattr(reg, "model_policy", None) or {}
    allow = policy.get("allow") if isinstance(policy, dict) else None
    if not allow:
        return None
    out = [str(x).strip() for x in allow if str(x).strip()]
    return out or None


def format_model_list(
    registry: ModelRegistry | None = None,
    *,
    active: set[str] | None = None,
) -> str:
    reg = registry or ModelRegistry.load()
    available = {m.id for m in reg.available_models()}
    allow = model_policy_allow(reg)
    active = active or set()
    lines = [
        "Models (• = ready, ★ = active pin):",
        "  Auth: api-key | codex (ChatGPT) | antigravity-cli (gemini CLI)",
    ]
    for mid, mc in reg.models.items():
        if allow is not None and mid not in allow:
            continue
        mark = "•" if mid in available else "○"
        pin = "★" if mid in active else " "
        roles = ",".join(mc.roles) if mc.roles else "-"
        src = reg.auth_source(mid)
        lines.append(
            f"  {mark}{pin} {mid}  ({mc.provider}/{mc.model})  [{roles}]  {{{src}}}"
        )
    lines.append("")
    lines.append(
        "Usage: /model <id> | /model planner <id> | /model executor <id> | "
        "/model reset [planner|executor]"
    )
    lines.append(
        "Tips: /model antigravity-flash (CLI 3.6) | /model gemini-flash (API) | "
        "/model gpt-codex | /model planner … /model executor …"
    )
    lines.append(
        "Note: /model <id> pins all roles. "
        "/model planner|executor pins only that slot. "
        "/model reset → role ladders again."
    )
    if allow is not None:
        lines.append(f"Policy allowlist: {', '.join(allow)}")
    return "\n".join(lines)


def model_status(
    override: str | None,
    *,
    once: str | None = None,
    role_overrides: dict[str, str] | None = None,
) -> str:
    roles = dict(role_overrides or {})
    parts: list[str] = []
    if once:
        parts.append(f"once={once}")
    if override:
        parts.append(f"session={override} (all roles)")
    if roles.get("planner"):
        parts.append(f"planner={roles['planner']}")
    if roles.get("executor"):
        parts.append(f"executor={roles['executor']}")
    if parts:
        return "Session model: " + ", ".join(parts)
    return "Session model: auto (role ladders — planner≠executor)"


def _active_ids(
    override: str | None,
    once: str | None,
    role_overrides: dict[str, str] | None,
) -> set[str]:
    out: set[str] = set()
    if once:
        out.add(once)
    if override:
        out.add(override)
    for mid in (role_overrides or {}).values():
        if mid:
            out.add(mid)
    return out


def _confirm_model_change(
    mid: str,
    *,
    scope: str,
    registry: ModelRegistry,
    role_note: str | None = None,
) -> str:
    mc = registry.models.get(mid)
    api = mc.model if mc else mid
    provider = mc.provider if mc else "?"
    src = registry.auth_source(mid)
    scope_line = role_note or "planner + executor (all roles) until /model reset"
    return (
        f"OK — {scope} model set to {mid}\n"
        f"  backend: {provider}/{api}  auth: {{{src}}}\n"
        f"  scope: {scope_line}\n"
        f"  verify: /model  (★ marks active) · next chat turn uses this pin"
    )


def _parse_flags(tokens: list[str]) -> tuple[list[str], set[str]]:
    flags: set[str] = set()
    rest: list[str] = []
    for tok in tokens:
        low = tok.lower()
        if low in {"--once", "-1"}:
            flags.add("once")
        elif low in {"--global", "-g"}:
            flags.add("global")
        elif low in {"--session", "-s"}:
            flags.add("session")
        else:
            rest.append(tok)
    return rest, flags


def _model_is_native_tool_caller(model_id: str, registry: ModelRegistry) -> bool:
    mc = registry.models.get(model_id)
    if mc is None:
        return False
    if "tool_calling" not in set(mc.capabilities or []):
        return False
    pc = registry.providers.get(mc.provider)
    if pc is not None and pc.protocol == "gemini_cli":
        return False
    return True


def _load_models_overlay() -> tuple[Path, dict[str, Any]]:
    path = kageha_home() / "models.yaml"
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = yaml.safe_load(path.read_text()) or {}
            if isinstance(loaded, dict):
                data = loaded
        except Exception:  # noqa: BLE001
            data = {}
    return path, data


def _pin_role_first(
    roles: dict[str, Any],
    role: str,
    model_id: str,
    *,
    base_ladder: list[str] | None = None,
) -> None:
    existing = list(roles.get(role) or base_ladder or [])
    roles[role] = [model_id] + [m for m in existing if m != model_id]


def persist_global_model(model_id: str, registry: ModelRegistry | None = None) -> Path:
    """Write ~/.kageha/models.yaml overlay pinning model_id first on role ladders.

    Antigravity / gemini-cli models are never pinned onto ``tool_calling`` —
    native tool loops require an API/Codex model.
    """
    return persist_setup_model_pins(
        session_default=model_id,
        planner=model_id,
        executor=model_id,
        registry=registry,
    )


def persist_setup_model_pins(
    *,
    session_default: str,
    planner: str,
    executor: str,
    registry: ModelRegistry | None = None,
) -> Path:
    """Pin planner + executor/subagent roles and session default (overwrite).

    - planner → ``planning`` (+ ``default`` when not already executor-only)
    - executor → ``tool_calling``, ``fast_worker``, ``monitor``, ``coding``
    - Antigravity / non-tool models are not pinned onto ``tool_calling``
    """
    reg = registry or ModelRegistry.load()
    path, data = _load_models_overlay()
    roles = dict(data.get("roles") or {})

    planner_id = (planner or session_default).strip()
    executor_id = (executor or session_default).strip()
    default_id = (session_default or planner_id or executor_id).strip()

    for role in SLOT_TO_ROLES["planner"]:
        _pin_role_first(
            roles,
            role,
            planner_id,
            base_ladder=list(reg.roles.get(role) or []),
        )

    exec_can_tools = _model_is_native_tool_caller(executor_id, reg)
    for role in SLOT_TO_ROLES["executor"]:
        if role == "tool_calling" and not exec_can_tools:
            # Keep API-capable ladder; do not put gemini-cli on tool_calling.
            ladder = list(roles.get(role) or reg.roles.get(role) or [])
            roles[role] = ladder or ["gemini-flash"]
            continue
        _pin_role_first(
            roles,
            role,
            executor_id,
            base_ladder=list(reg.roles.get(role) or []),
        )

    if "default" not in roles or not roles["default"]:
        roles["default"] = [default_id]
    if "tool_calling" not in roles or not roles["tool_calling"]:
        roles["tool_calling"] = [
            m
            for m in (reg.roles.get("tool_calling") or ["gemini-flash", "gemini-pro"])
            if _model_is_native_tool_caller(m, reg)
        ] or ["gemini-flash"]

    data["roles"] = roles
    data["session_default_model"] = default_id
    data["setup_pins"] = {
        "planner": planner_id,
        "executor": executor_id,
        "session_default": default_id,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def _credential_hint(mid: str, reg: ModelRegistry) -> str:
    pc = reg.providers.get(reg.models[mid].provider)
    env = pc.api_key_env if pc else "API key"
    pname = pc.name if pc else ""
    hint = f"Set {env}, then retry /model {mid}."
    if pname in {"openai-codex", "openai"} or (env or "").upper().startswith("OPENAI"):
        hint = (
            "Import ChatGPT/Codex auth (after `codex login`):\n"
            "  kageha models auth import chatgpt\n"
            f"Or set {env}. Then retry /model {mid}."
        )
    elif pname in {"gemini", "google"} or (env or "").upper().startswith("GEMINI"):
        hint = (
            f"Set {env} for Gemini API (full tools), or use Antigravity CLI:\n"
            "  /model antigravity\n"
            f"Then retry /model {mid}."
        )
    elif pname == "antigravity" or (env or "") == "ANTIGRAVITY_CLI":
        hint = (
            "Install Gemini CLI, sign in (Antigravity or `gemini`), ensure "
            "`gemini` is on PATH, then retry /model antigravity "
            "(or antigravity-flash)."
        )
    return hint


def _ensure_ready(
    mid: str,
    *,
    reg: ModelRegistry,
    override: str | None,
    once: str | None,
    role_overrides: dict[str, str],
) -> ModelCommandResult | None:
    allow = model_policy_allow(reg)
    if allow is not None and mid not in allow:
        return ModelCommandResult(
            handled=True,
            override=override,
            once=once,
            role_overrides=role_overrides,
            message=(
                f"Model {mid} is outside model_policy.allow "
                f"({', '.join(allow)}). Pick an allowed model."
            ),
        )
    available = {m.id for m in reg.available_models()}
    if mid not in available:
        return ModelCommandResult(
            handled=True,
            override=override,
            once=once,
            role_overrides=role_overrides,
            message=(
                f"Model {mid} is configured but credentials are missing.\n"
                + _credential_hint(mid, reg)
            ),
        )
    return None


def handle_model_command(
    line: str,
    *,
    override: str | None,
    once: str | None = None,
    role_overrides: dict[str, str] | None = None,
    workspace: SessionWorkspace | None = None,
    registry: ModelRegistry | None = None,
) -> ModelCommandResult:
    """Handle /model …. Supports all-roles pin and planner/executor slots."""
    text = (line or "").strip()
    low = text.lower()
    if low not in {"/model", "/models"} and not low.startswith("/model ") and not low.startswith(
        "/models "
    ):
        return ModelCommandResult(
            handled=False,
            override=override,
            once=once,
            role_overrides=dict(role_overrides or {}),
        )

    roles = dict(role_overrides or {})
    if workspace is not None and not roles:
        roles = workspace.get_model_role_overrides()

    arg = text.split(maxsplit=1)[1].strip() if " " in text else ""
    reg = registry or ModelRegistry.load()

    def _result(
        *,
        handled: bool = True,
        ov: str | None = None,
        onc: str | None = None,
        ro: dict[str, str] | None = None,
        message: str = "",
        changed: bool = False,
    ) -> ModelCommandResult:
        return ModelCommandResult(
            handled=handled,
            override=ov if ov is not None or changed else override,
            once=onc if onc is not None or changed else once,
            role_overrides=dict(ro if ro is not None else roles),
            message=message,
            changed=changed,
        )

    if not arg or arg.lower() in {"status", "show", "current"}:
        return _result(
            message=model_status(override, once=once, role_overrides=roles)
            + "\n"
            + format_model_list(
                reg, active=_active_ids(override, once, roles)
            ),
        )

    if arg.lower() in {"list", "ls"}:
        return _result(
            message=format_model_list(
                reg, active=_active_ids(override, once, roles)
            ),
        )

    tokens = arg.split()
    rest, flags = _parse_flags(tokens)
    if not rest:
        return _result(
            message="Usage: /model <id> | /model planner <id> | /model executor <id>\n"
            + format_model_list(reg),
        )

    # /model reset [planner|executor]
    if rest[0].lower() in {"reset", "auto", "clear", "default", "off"}:
        slot = resolve_slot(rest[1]) if len(rest) > 1 else None
        if len(rest) > 1 and slot is None:
            return _result(
                message="Usage: /model reset | /model reset planner | /model reset executor",
            )
        if slot:
            roles = dict(roles)
            roles.pop(slot, None)
            if workspace is not None:
                workspace.set_model_role_override(slot, None)
            return _result(
                ov=override,
                onc=once,
                ro=roles,
                changed=True,
                message=(
                    f"OK — {slot} pin cleared.\n"
                    + model_status(override, once=once, role_overrides=roles)
                ),
            )
        if workspace is not None:
            workspace.set_model_override(None)
            workspace.set_model_once(None)
            workspace.set_model_role_overrides(None)
        return _result(
            ov=None,
            onc=None,
            ro={},
            changed=True,
            message=(
                "OK — all model pins cleared.\n"
                + model_status(None)
                + "\nPlanner → roles.planning; executor → roles.tool_calling / fast_worker."
            ),
        )

    # /model planner <id> | /model executor <id>
    slot = resolve_slot(rest[0])
    if slot is not None:
        if len(rest) < 2:
            return _result(
                message=f"Usage: /model {slot} <model-id>\n"
                + model_status(override, once=once, role_overrides=roles),
            )
        if rest[1].lower() in {"reset", "auto", "clear", "off"}:
            roles = dict(roles)
            roles.pop(slot, None)
            if workspace is not None:
                workspace.set_model_role_override(slot, None)
            return _result(
                ov=override,
                onc=once,
                ro=roles,
                changed=True,
                message=(
                    f"OK — {slot} pin cleared.\n"
                    + model_status(override, once=once, role_overrides=roles)
                ),
            )
        mid = resolve_model_id(" ".join(rest[1:]), reg)
        if not mid:
            return _result(
                message=f"Unknown model: {' '.join(rest[1:])}\n" + format_model_list(reg),
            )
        bad = _ensure_ready(
            mid, reg=reg, override=override, once=once, role_overrides=roles
        )
        if bad:
            return bad
        if flags & {"once", "global"}:
            return _result(
                message=(
                    f"--once/--global not supported with /model {slot}. "
                    f"Use `/model {slot} <id>` (session) or `/model <id> --global` (all roles)."
                ),
            )
        # Split mode: clear all-roles pin so planner≠executor is unambiguous.
        roles = dict(roles)
        roles[slot] = mid
        if workspace is not None:
            workspace.set_model_override(None)
            workspace.set_model_once(None)
            workspace.set_model_role_overrides(roles)
        note = (
            f"{slot} only "
            f"(router roles: {', '.join(SLOT_TO_ROLES[slot])}); "
            "other slot stays auto unless set"
        )
        return _result(
            ov=None,
            onc=None,
            ro=roles,
            changed=True,
            message=_confirm_model_change(
                mid, scope=f"session {slot}", registry=reg, role_note=note
            ),
        )

    token = " ".join(rest)
    mid = resolve_model_id(token, reg)
    if not mid:
        return _result(
            message=f"Unknown model: {token}\n" + format_model_list(reg),
        )

    bad = _ensure_ready(
        mid, reg=reg, override=override, once=once, role_overrides=roles
    )
    if bad:
        return bad

    # All-roles pin clears slot pins.
    if "once" in flags:
        if workspace is not None:
            workspace.set_model_once(mid)
            workspace.set_model_role_overrides(None)
        return _result(
            ov=override,
            onc=mid,
            ro={},
            changed=True,
            message=_confirm_model_change(
                mid, scope="once (next turn only)", registry=reg
            ),
        )

    if "global" in flags:
        path = persist_global_model(mid, reg)
        if workspace is not None:
            workspace.set_model_override(mid)
            workspace.set_model_once(None)
            workspace.set_model_role_overrides(None)
        return _result(
            ov=mid,
            onc=None,
            ro={},
            changed=True,
            message=(
                _confirm_model_change(mid, scope="global+session", registry=reg)
                + f"\n  saved: {path}"
            ),
        )

    if workspace is not None:
        workspace.set_model_override(mid)
        workspace.set_model_once(None)
        workspace.set_model_role_overrides(None)
    return _result(
        ov=mid,
        onc=None,
        ro={},
        changed=True,
        message=_confirm_model_change(mid, scope="session", registry=reg),
    )
