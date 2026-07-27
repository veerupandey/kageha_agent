"""Provider / model readiness doctor (beyond smoke test)."""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

from kageha.config import models_yaml_paths, read_env_value, tools_policy_paths
from kageha.harness.shell_sandbox import sandbox_status, workspace_access
from kageha.harness.tool_policy import load_tools_policy
from kageha.models.registry import ModelRegistry


REQUIRED_ROLES = (
    "default",
    "planning",
    "fast_worker",
    "tool_calling",
    "monitor",
    "coding",
)


@dataclass
class DoctorCheck:
    name: str
    ok: bool
    detail: str
    severity: str = "error"  # error | warn | info


@dataclass
class DoctorReport:
    ok: bool
    checks: list[DoctorCheck] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [asdict(c) for c in self.checks],
        }


def run_models_doctor(
    *,
    smoke: bool = True,
    model_id: str | None = None,
) -> DoctorReport:
    checks: list[DoctorCheck] = []
    reg = ModelRegistry.load()

    # Registry paths
    paths = [str(p) for p in models_yaml_paths() if p.is_file()]
    checks.append(
        DoctorCheck(
            name="registry_paths",
            ok=bool(paths),
            detail=", ".join(paths) if paths else "no models.yaml found",
            severity="error" if not paths else "info",
        )
    )
    checks.append(
        DoctorCheck(
            name="providers",
            ok=bool(reg.providers),
            detail=f"{len(reg.providers)} providers, {len(reg.models)} models",
            severity="error" if not reg.providers else "info",
        )
    )

    available = {m.id for m in reg.available_models()}
    checks.append(
        DoctorCheck(
            name="available_models",
            ok=bool(available),
            detail=", ".join(sorted(available)) if available else "(none)",
            severity="error" if not available else "info",
        )
    )

    # Role ladders (hard requirement)
    role_problems: list[str] = []
    for role in REQUIRED_ROLES:
        ladder = list(reg.roles.get(role) or [])
        if not ladder:
            role_problems.append(f"{role}: empty")
            continue
        if not any(mid in available for mid in ladder):
            role_problems.append(f"{role}: no available model ({', '.join(ladder[:4])})")
    checks.append(
        DoctorCheck(
            name="roles",
            ok=not role_problems,
            detail="; ".join(role_problems) if role_problems else "all required roles covered",
            severity="error" if role_problems else "info",
        )
    )

    # Keys — warn for unused fallbacks; error only when a role is uncovered
    # (roles check above already hard-fails). Missing optional provider keys = warn.
    missing_keys: list[str] = []
    file_only: list[str] = []
    for pname, pc in reg.providers.items():
        env = pc.api_key_env
        in_env = bool(os.environ.get(env, "").strip())
        in_file = bool(read_env_value(env))
        if in_env:
            continue
        used = any(m.provider == pname for m in reg.models.values())
        if not used:
            continue
        if in_file:
            file_only.append(f"{pname}({env})")
        else:
            missing_keys.append(f"{pname}({env})")
    if missing_keys:
        checks.append(
            DoctorCheck(
                name="api_keys",
                ok=True,
                detail="missing (optional/fallback ok if roles covered): "
                + ", ".join(missing_keys),
                severity="warn",
            )
        )
    elif file_only:
        checks.append(
            DoctorCheck(
                name="api_keys",
                ok=True,
                detail="in .env but not loaded into process: " + ", ".join(file_only),
                severity="warn",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                name="api_keys",
                ok=True,
                detail="all used providers have keys in env",
                severity="info",
            )
        )

    # Sandbox
    st = sandbox_status()
    sand_ok = st.available or st.profile == "off"
    checks.append(
        DoctorCheck(
            name="sandbox",
            ok=sand_ok,
            detail=(
                f"profile={st.profile} available={st.available} "
                f"workspace={workspace_access()} — {st.detail}"
            ),
            severity="warn" if not sand_ok else "info",
        )
    )

    # Web search backend
    from kageha.config import (
        env_key,
        web_search_backend,
        web_search_keyed_providers,
        web_search_perplexity_key,
        web_search_tavily_key,
    )

    backend = web_search_backend()
    brave = bool(env_key("BRAVE_API_KEY") or os.environ.get("BRAVE_SEARCH_API_KEY"))
    tavily = bool(web_search_tavily_key())
    perplexity = bool(web_search_perplexity_key())
    gemini = bool(env_key("GEMINI_API_KEY"))
    keyed = web_search_keyed_providers()
    detail = f"backend={backend}"
    if backend in {"brave", "tavily", "perplexity"}:
        key_ok = {"brave": brave, "tavily": tavily, "perplexity": perplexity}[backend]
        detail += " (key set)" if key_ok else " (key missing — will fall back)"
        others = [p for p in keyed if p != backend]
        if others:
            detail += f"; also {','.join(others)}"
        detail += " → Gemini → DDG"
    elif backend == "gemini":
        detail += " (+ DDG fallback)" + (" via GEMINI_API_KEY" if gemini else " — no Gemini key")
        if keyed:
            detail += f"; keyed unused ({','.join(keyed)})"
    elif backend == "ddg":
        detail += " (DuckDuckGo only)"
    checks.append(
        DoctorCheck(
            name="web_search",
            ok=True,
            detail=detail,
            severity="info",
        )
    )

    # Media providers (optional)
    fal = bool(env_key("FAL_KEY") or env_key("FAL_API_KEY"))
    media_bits = [
        f"GEMINI_API_KEY={'yes' if gemini else 'no'} (gemini_generate_image / Nano Banana Pro)",
        f"FAL_KEY={'yes' if fal else 'no'} (fal_* image/video)",
        f"SILICONFLOW_API_KEY={'yes' if bool(env_key('SILICONFLOW_API_KEY')) else 'no'}",
    ]
    checks.append(
        DoctorCheck(
            name="media",
            ok=True,
            detail="; ".join(media_bits),
            severity="info",
        )
    )

    # tools.yaml + pack gating
    from kageha.harness.tool_packs import (
        CORE_PACK_NAMES,
        OPTIONAL_PACK_NAMES,
        resolve_enabled_packs,
        summarize_packs,
    )

    pol = load_tools_policy()
    tpaths = [str(p) for p in tools_policy_paths() if p.is_file()]
    deny = pol.get("deny") or []
    allow = pol.get("allow") or []
    enabled = resolve_enabled_packs(policy=pol)
    opt_on = [p for p in enabled if p in OPTIONAL_PACK_NAMES]
    checks.append(
        DoctorCheck(
            name="tools_policy",
            ok=True,
            detail=(
                f"paths={', '.join(tpaths) or '(defaults)'} "
                f"allow={len(allow)} deny={len(deny)}"
            ),
            severity="info",
        )
    )
    checks.append(
        DoctorCheck(
            name="tool_packs",
            ok=True,
            detail=(
                f"{summarize_packs(enabled)}; "
                f"core_always={len(CORE_PACK_NAMES)}; "
                f"optional_on={len(opt_on)}; "
                f"hint=KAGEHA_TOOL_PACKS=all|browser,media or tools.yaml packs"
            ),
            severity="info",
        )
    )
    if "computer" in enabled:
        try:
            from kageha.harness.tools import computer_driver as _cua

            bin_path = _cua.driver_bin()
            if not bin_path:
                checks.append(
                    DoctorCheck(
                        name="computer_driver",
                        ok=False,
                        detail="cua-driver missing — run scripts/install_computer_driver.sh",
                        severity="warn",
                    )
                )
            else:
                import asyncio as _aio

                perms = _aio.run(_cua.permissions_status())
                ready = bool(
                    perms.get("accessibility") and perms.get("screen_recording")
                )
                checks.append(
                    DoctorCheck(
                        name="computer_driver",
                        ok=ready,
                        detail=(
                            f"bin={bin_path} accessibility={perms.get('accessibility')} "
                            f"screen_recording={perms.get('screen_recording')}"
                            + (
                                ""
                                if ready
                                else " — run: cua-driver permissions grant"
                            )
                        ),
                        severity="info" if ready else "warn",
                    )
                )
        except Exception as exc:  # noqa: BLE001
            checks.append(
                DoctorCheck(
                    name="computer_driver",
                    ok=False,
                    detail=f"probe failed: {exc}",
                    severity="warn",
                )
            )

    # Smoke — role-ladder heads only (not every catalog model)
    if smoke:
        if model_id:
            ids = [model_id]
        else:
            ids = []
            seen: set[str] = set()
            for role in ("default", "planning", "fast_worker"):
                for mid in (reg.roles.get(role) or [])[:2]:
                    if mid in available and mid not in seen:
                        ids.append(mid)
                        seen.add(mid)
            if not ids:
                ids = sorted(available)[:3]
        if not ids:
            checks.append(
                DoctorCheck(
                    name="smoke",
                    ok=False,
                    detail="no available models to smoke-test",
                    severity="error",
                )
            )
        else:
            smoke_ok, smoke_detail = _smoke_models(reg, ids)
            checks.append(
                DoctorCheck(
                    name="smoke",
                    ok=smoke_ok,
                    detail=smoke_detail,
                    severity="error" if not smoke_ok else "info",
                )
            )
    else:
        checks.append(
            DoctorCheck(
                name="smoke",
                ok=True,
                detail="skipped (--no-smoke)",
                severity="info",
            )
        )

    hard_fail = any(c.severity == "error" and not c.ok for c in checks)
    return DoctorReport(ok=not hard_fail, checks=checks)


def _smoke_models(reg: ModelRegistry, ids: list[str]) -> tuple[bool, str]:
    per_model = float(
        (os.environ.get("KAGEHA_MODELS_SMOKE_TIMEOUT") or "25").strip() or "25"
    )

    async def _smoke_one(mid: str) -> tuple[str, str | None]:
        try:
            model = reg.build(mid)
            text = await asyncio.wait_for(model.smoke(), timeout=max(5.0, per_model))
            if not text:
                return mid, "empty"
            return mid, None
        except Exception as exc:  # noqa: BLE001
            return mid, str(exc)

    async def _go() -> tuple[bool, str]:
        fails: list[str] = []
        oks: list[str] = []
        for mid in ids:
            name, err = await _smoke_one(mid)
            if err:
                fails.append(f"{name}: {err}")
            else:
                oks.append(name)
        # Working agent: pass if at least one ladder model smokes
        if oks:
            detail = "OK " + ", ".join(oks)
            if fails:
                detail += " | skip " + "; ".join(fails[:4])
            return True, detail
        return False, "FAIL " + "; ".join(fails[:6])

    return asyncio.run(_go())


def format_doctor_report(report: DoctorReport, *, rich: bool = True) -> str:
    """Render doctor report; uses Rich colors when available."""
    if rich:
        try:
            return _format_doctor_rich(report)
        except Exception:  # noqa: BLE001
            pass
    lines = [f"doctor: {'OK' if report.ok else 'FAIL'}"]
    for c in report.checks:
        mark = "✓" if c.ok else ("!" if c.severity == "warn" else "✗")
        lines.append(f"  {mark} [{c.severity}] {c.name}: {c.detail}")
    return "\n".join(lines)


def _format_doctor_rich(report: DoctorReport) -> str:
    from io import StringIO

    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    buf = StringIO()
    console = Console(file=buf, force_terminal=True, color_system="truecolor", width=100)
    title = Text("doctor: ", style="bold")
    title.append("OK" if report.ok else "FAIL", style="bold green" if report.ok else "bold red")
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Status", width=8)
    table.add_column("Check", style="cyan")
    table.add_column("Detail")
    for c in report.checks:
        if c.ok and c.severity == "info":
            mark = Text("✓", style="green")
        elif c.ok and c.severity == "warn":
            mark = Text("!", style="yellow")
        elif c.ok:
            mark = Text("✓", style="green")
        else:
            mark = Text("✗", style="red")
        table.add_row(mark, c.name, c.detail)
    console.print(Panel(table, title=title, border_style="blue" if report.ok else "red"))
    return buf.getvalue().rstrip() + "\n"


def maybe_fix_interactive(report: DoctorReport) -> None:
    """If keys/roles failed, offer to jump into models setup."""
    bad = [c for c in report.checks if not c.ok and c.name in {"api_keys", "roles", "available_models"}]
    if not bad:
        return
    if not sys.stdin.isatty():
        return
    try:
        ans = input("Open interactive models setup to fix? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print(flush=True)
        return
    if ans not in {"y", "yes"}:
        return
    from kageha.models.setup import run_models_setup

    run_models_setup(smoke_test=False)
