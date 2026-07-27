"""Operational readiness and dependency probes for the durable runtime."""

from __future__ import annotations

import asyncio
import platform
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

from kageha.config import project_root
from kageha.harness.shell_sandbox import sandbox_status
from kageha.runtime.providers import ProviderControlPlane
from kageha.runtime.store import RuntimeStore
from kageha.runtime.supervisor import ServiceSupervisor


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    severity: str = "error"


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(
            not check.ok and check.severity == "error"
            for check in self.checks
        )

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": [asdict(check) for check in self.checks]}


def run_doctor(*, deep: bool = False) -> DoctorReport:
    checks: list[Check] = []
    version_ok = (3, 11) <= sys.version_info[:2] <= (3, 13)
    checks.append(
        Check(
            "python",
            version_ok,
            f"{platform.python_version()} executable={sys.executable}",
        )
    )
    uv_lock = project_root() / "uv.lock"
    checks.append(
        Check(
            "uv_lock",
            uv_lock.is_file() and bool(shutil.which("uv")),
            f"uv={shutil.which('uv') or 'missing'} lock={uv_lock}",
        )
    )

    store: RuntimeStore | None = None
    try:
        store = RuntimeStore()
        integrity = str(
            store._conn.execute("PRAGMA integrity_check").fetchone()[0]  # noqa: SLF001
        )
        status = store.status()
        checks.append(
            Check(
                "runtime_db",
                integrity == "ok" and status["wal"],
                (
                    f"integrity={integrity} wal={status['wal']} "
                    f"schema={status['schema_version']} path={status['database']}"
                ),
            )
        )
        if deep:
            replay_failures: list[str] = []
            for session in store.list_sessions(limit=100):
                try:
                    store.rebuild(str(session["id"]))
                except Exception as exc:  # noqa: BLE001
                    replay_failures.append(f"{session['id']}: {exc}")
            checks.append(
                Check(
                    "event_replay",
                    not replay_failures,
                    (
                        "all sessions rebuilt"
                        if not replay_failures
                        else "; ".join(replay_failures[:5])
                    ),
                )
            )

        provider_control = ProviderControlPlane(store)
        health = asyncio.run(provider_control.check_all(deep=deep))
        healthy = {item.provider for item in health if item.available}
        # Require at least one provider from the default role ladder (not a
        # hard-coded openai+gemini+siliconflow set — unused providers are WARN).
        try:
            from kageha.models.registry import ModelRegistry

            reg = ModelRegistry.load()
            ladder: list[str] = []
            for mid in (reg.roles.get("default") or [])[:6]:
                mc = reg.models.get(mid)
                if mc and mc.provider not in ladder:
                    ladder.append(mc.provider)
            if not ladder:
                ladder = sorted(healthy)[:3] or ["gemini"]
        except Exception:  # noqa: BLE001
            ladder = ["gemini", "siliconflow"]
        ladder_ok = bool(healthy.intersection(ladder))
        missing_ladder = [p for p in ladder if p not in healthy]
        checks.append(
            Check(
                "providers",
                ladder_ok,
                (
                    f"healthy={','.join(sorted(healthy)) or 'none'} "
                    f"default_ladder={','.join(ladder)}"
                    + (
                        f" missing={','.join(missing_ladder)}"
                        if missing_ladder and ladder_ok
                        else ""
                    )
                ),
                severity="error" if not ladder_ok else "info",
            )
        )
        unused_required_noise = {"openai", "siliconflow", "anthropic"} - set(ladder)
        stale = sorted(p for p in unused_required_noise if p not in healthy)
        if stale and ladder_ok:
            checks.append(
                Check(
                    "providers_optional",
                    True,
                    f"unused providers not healthy (ok): {','.join(stale)}",
                    severity="info",
                )
            )

        # Failover-ready when ≥2 distinct providers on the default ladder are healthy.
        try:
            from kageha.models.registry import ModelRegistry

            reg = ModelRegistry.load()
            ladder_providers: list[str] = []
            for mid in reg.roles.get("default") or []:
                mc = reg.models.get(mid)
                if mc and mc.provider not in ladder_providers:
                    ladder_providers.append(mc.provider)
            healthy_ladder = [p for p in ladder_providers if p in healthy]
            failover_ready = len(healthy_ladder) >= 2
            checks.append(
                Check(
                    "failover_ready",
                    True,
                    (
                        f"healthy_ladder_providers={','.join(healthy_ladder) or 'none'} "
                        f"({'ready' if failover_ready else 'single-provider — add another key for failover'})"
                    ),
                    severity="info",
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(
                Check(
                    "failover_ready",
                    True,
                    f"could not evaluate: {exc}",
                    severity="info",
                )
            )

        sandbox = sandbox_status()
        strict_ok = sandbox.profile != "off" and sandbox.available
        checks.append(
            Check(
                "strict_isolation",
                strict_ok,
                f"profile={sandbox.profile} available={sandbox.available}: {sandbox.detail}",
                severity="error",
            )
        )
        checks.append(
            Check(
                "approval_fallback",
                True,
                (
                    "available; safety rating is capped below 5/5"
                    if not strict_ok
                    else "available; strict isolation also healthy"
                ),
                severity="info",
            )
        )

        supervisor = ServiceSupervisor(store=store)
        service_status = supervisor.status()
        checks.append(
            Check(
                "supervisor",
                not service_status["duplicate_pids"],
                (
                    f"services={len(service_status['services'])} "
                    f"duplicate_pids={service_status['duplicate_pids']}"
                ),
            )
        )
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        checks.append(Check("runtime_db", False, str(exc)))
    finally:
        if store is not None:
            store.close()
    return DoctorReport(checks)

