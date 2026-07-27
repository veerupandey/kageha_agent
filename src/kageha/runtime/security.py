"""Security-profile decisions shared by the execution envelope."""

from __future__ import annotations

import json
from dataclasses import dataclass

from kageha.harness.shell_sandbox import sandbox_status
from kageha.runtime.types import SecurityProfile


_ISOLATION_RISKS = {
    "browser",
    "network",
    "forged",
    "mcp",
    "computer_input",
    "shell_network_or_destructive",
    "shell_elevated",
}


@dataclass(frozen=True)
class SecurityDecision:
    allowed: bool
    sandboxed: bool
    profile: SecurityProfile
    reason: str

    @property
    def grant(self) -> str:
        return json.dumps(
            {
                "allowed": self.allowed,
                "sandboxed": self.sandboxed,
                "profile": self.profile.value,
                "reason": self.reason,
            },
            sort_keys=True,
        )


class ExecutionSecurityPolicy:
    def __init__(self, profile: SecurityProfile) -> None:
        self.profile = profile

    def assess(self, *, risk_class: str) -> SecurityDecision:
        risk = (risk_class or "safe").strip().lower()
        status = sandbox_status()
        isolation_available = status.profile != "off" and status.available
        requires_isolation = risk in _ISOLATION_RISKS
        if (
            self.profile == SecurityProfile.STRICT
            and requires_isolation
            and not isolation_available
        ):
            return SecurityDecision(
                allowed=False,
                sandboxed=False,
                profile=self.profile,
                reason=(
                    f"strict profile denied {risk}: isolation unavailable "
                    f"({status.profile}: {status.detail})"
                ),
            )
        return SecurityDecision(
            allowed=True,
            sandboxed=isolation_available if requires_isolation else True,
            profile=self.profile,
            reason=(
                f"isolation={status.profile}"
                if isolation_available
                else (
                    "approved fallback without OS isolation"
                    if requires_isolation
                    else "in-process safe operation"
                )
            ),
        )

