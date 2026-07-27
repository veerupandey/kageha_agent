"""Branch / PR review + babysit loop (Bugbot / ultrareview lite)."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kageha.project.brain import resolve_project_root

_FINDING_RE = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?(?:severityseverity|HIGH|MEDIUM|LOW|INFO)\b.*$"
)


@dataclass
class ReviewFinding:
    severity: str
    summary: str
    path: str = ""
    line: int | None = None


@dataclass
class ReviewResult:
    ok: bool
    base: str
    head: str
    diff_stat: str
    findings: list[ReviewFinding] = field(default_factory=list)
    message: str = ""
    promoted_rules: list[str] = field(default_factory=list)
    diff: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "base": self.base,
            "head": self.head,
            "diff_stat": self.diff_stat,
            "diff": self.diff,
            "findings": [
                {
                    "severity": f.severity,
                    "summary": f.summary,
                    "path": f.path,
                    "line": f.line,
                }
                for f in self.findings
            ],
            "message": self.message,
            "promoted_rules": self.promoted_rules,
        }


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )


def collect_diff(
    project_root: str | Path,
    *,
    base: str = "main",
    head: str = "HEAD",
    max_chars: int = 80_000,
) -> tuple[str, str]:
    root = resolve_project_root(project_root)
    if root is None:
        raise ValueError("invalid project_root")
    # Prefer merge-base diff when base exists.
    base_ref = base
    probe = _git(root, "rev-parse", "--verify", base)
    if probe.returncode != 0:
        for alt in ("master", "origin/main", "origin/master", "HEAD~1"):
            if _git(root, "rev-parse", "--verify", alt).returncode == 0:
                base_ref = alt
                break
    stat = _git(root, "diff", "--stat", f"{base_ref}...{head}")
    full = _git(root, "diff", "--unified=3", f"{base_ref}...{head}")
    diff = full.stdout or ""
    if len(diff) > max_chars:
        diff = diff[: max_chars - 20] + "\n…[truncated]"
    return (stat.stdout or "").strip(), diff


async def run_review(
    *,
    project_root: str | Path,
    base: str = "main",
    head: str = "HEAD",
    promote_rules: bool = False,
    auto_approve: bool = True,
    max_steps: int = 16,
) -> ReviewResult:
    from kageha.config import security_profile
    from kageha.runtime import AgentRuntime, SecurityProfile, TurnRequest

    root = resolve_project_root(project_root)
    if root is None:
        raise ValueError("invalid project_root")
    diff_stat, diff = collect_diff(root, base=base, head=head)
    if not diff.strip():
        return ReviewResult(
            ok=True,
            base=base,
            head=head,
            diff_stat=diff_stat or "(empty)",
            diff="",
            message="No diff to review.",
        )

    prompt = (
        "You are performing a defect-first code review of a git diff.\n"
        f"Base: {base}  Head: {head}\n"
        "Return a concise report with findings as markdown bullets, each starting "
        "with CRITICAL|HIGH|MEDIUM|LOW|INFO, then a one-line summary and file path.\n"
        "Focus on bugs, security, regressions, and missing tests. Skip style nits.\n\n"
        f"## Diffstat\n```\n{diff_stat}\n```\n\n"
        f"## Diff\n```diff\n{diff}\n```\n"
    )
    runtime = AgentRuntime()
    try:
        result = await runtime.execute(
            TurnRequest(
                objective=prompt,
                user_id="local",
                agent_id="review",
                project_root=str(root),
                auto_approve=auto_approve,
                security_profile=SecurityProfile(security_profile()),
                max_steps=max_steps,
                live=False,
                platform="review",
                loop_mode="followup",
                agent_mode="normal",
                system_extra=(
                    "Review mode: read the diff carefully. Prefer listing concrete "
                    "findings over rewriting the code unless a tiny fix is obvious."
                ),
            )
        )
        message = result.message or ""
        status_ok = str(result.status or "").lower() in {
            "success",
            "ok",
            "completed",
        }
    finally:
        runtime.close()

    findings = _parse_findings(message)
    promoted: list[str] = []
    if promote_rules and findings:
        promoted = promote_findings_to_rules(root, findings)

    return ReviewResult(
        ok=status_ok,
        base=base,
        head=head,
        diff_stat=diff_stat,
        diff=diff,
        findings=findings,
        message=message,
        promoted_rules=promoted,
    )


def _parse_findings(text: str) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    for line in (text or "").splitlines():
        if not _FINDING_RE.match(line):
            continue
        sev = "INFO"
        for token in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"):
            if token.lower() in line.lower():
                sev = token
                break
        # crude path extraction
        path = ""
        m = re.search(r"`([^`]+\.[a-zA-Z0-9]+)`", line)
        if m:
            path = m.group(1)
        else:
            m2 = re.search(r"(\S+\.[a-zA-Z0-9]{1,8}:\d+)", line)
            if m2:
                path = m2.group(1)
        findings.append(
            ReviewFinding(severity=sev, summary=line.strip()[:400], path=path)
        )
    return findings[:40]


def promote_findings_to_rules(
    project_root: Path,
    findings: list[ReviewFinding],
    *,
    limit: int = 5,
) -> list[str]:
    """Write high-severity findings into .kageha/rules/learned-*.md."""
    rules_dir = project_root / ".kageha" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    severe = [f for f in findings if f.severity in {"CRITICAL", "HIGH", "MEDIUM"}]
    for i, finding in enumerate(severe[:limit]):
        name = f"learned-review-{i+1}.md"
        path = rules_dir / name
        glob = "**/*" if not finding.path else finding.path.split(":")[0]
        body = (
            "---\n"
            f"globs: [{glob}]\n"
            "---\n\n"
            f"# Learned review rule\n\n"
            f"- Severity: {finding.severity}\n"
            f"- {finding.summary}\n"
            "\nAvoid repeating this class of defect in future changes.\n"
        )
        try:
            path.write_text(body, encoding="utf-8")
            written.append(str(path.relative_to(project_root)))
        except OSError:
            continue
    return written


@dataclass
class BabysitResult:
    pr: str
    ok: bool
    rounds: int
    status: str
    message: str
    checks: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pr": self.pr,
            "ok": self.ok,
            "rounds": self.rounds,
            "status": self.status,
            "message": self.message,
            "checks": self.checks,
        }


async def babysit_pr(
    *,
    pr: str,
    project_root: str | Path | None = None,
    max_rounds: int = 3,
    auto_approve: bool = True,
) -> BabysitResult:
    """Poll PR checks via gh and optionally ask the agent to fix failures."""
    root = resolve_project_root(project_root) or Path.cwd()
    rounds = max(1, min(int(max_rounds), 8))
    checks_history: list[dict[str, Any]] = []

    for i in range(rounds):
        proc = subprocess.run(
            ["gh", "pr", "checks", str(pr), "--json", "name,state,bucket,link"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            # Fallback: pr view
            view = subprocess.run(
                ["gh", "pr", "view", str(pr), "--json", "state,title,statusCheckRollup"],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=False,
            )
            if view.returncode != 0:
                return BabysitResult(
                    pr=str(pr),
                    ok=False,
                    rounds=i + 1,
                    status="error",
                    message=(proc.stderr or view.stderr or "gh pr checks failed").strip(),
                )
            payload = json.loads(view.stdout or "{}")
            checks_history.append(payload)
            state = str(payload.get("state") or "")
            if state.upper() == "MERGED":
                return BabysitResult(
                    pr=str(pr),
                    ok=True,
                    rounds=i + 1,
                    status="merged",
                    message="PR already merged.",
                    checks=checks_history,
                )
            rollup = payload.get("statusCheckRollup") or []
            failing = [
                c
                for c in rollup
                if isinstance(c, dict)
                and str(c.get("state") or c.get("conclusion") or "").upper()
                in {"FAILURE", "FAILED", "ERROR", "CANCELLED"}
            ]
            if not failing and rollup:
                return BabysitResult(
                    pr=str(pr),
                    ok=True,
                    rounds=i + 1,
                    status="green",
                    message="Checks look green.",
                    checks=checks_history,
                )
            summary = json.dumps(failing or rollup, indent=2)[:4000]
        else:
            rows = json.loads(proc.stdout or "[]")
            checks_history.append({"checks": rows})
            failing = [
                r
                for r in rows
                if str(r.get("bucket") or r.get("state") or "").lower()
                in {"fail", "pending", "failure", "failed"}
            ]
            hard_fail = [
                r
                for r in rows
                if str(r.get("bucket") or "").lower() == "fail"
                or str(r.get("state") or "").upper() in {"FAILURE", "FAILED"}
            ]
            if rows and not hard_fail and not any(
                str(r.get("bucket") or "").lower() == "pending" for r in rows
            ):
                return BabysitResult(
                    pr=str(pr),
                    ok=True,
                    rounds=i + 1,
                    status="green",
                    message="All PR checks passed.",
                    checks=checks_history,
                )
            if not hard_fail:
                # still pending
                summary = json.dumps(failing or rows, indent=2)[:4000]
                if i + 1 >= rounds:
                    return BabysitResult(
                        pr=str(pr),
                        ok=False,
                        rounds=i + 1,
                        status="pending",
                        message="Checks still pending after max rounds.",
                        checks=checks_history,
                    )
                await _sleep(20)
                continue
            summary = json.dumps(hard_fail, indent=2)[:4000]

        # Ask agent to fix
        from kageha.config import security_profile
        from kageha.runtime import AgentRuntime, SecurityProfile, TurnRequest

        runtime = AgentRuntime()
        try:
            await runtime.execute(
                TurnRequest(
                    objective=(
                        f"Babysit PR {pr}. Failing or pending checks:\n{summary}\n\n"
                        "Inspect CI failures, fix the code in this repo, commit if "
                        "appropriate, and push. Stop when checks should pass or you "
                        "are blocked."
                    ),
                    user_id="local",
                    agent_id="babysit",
                    project_root=str(root),
                    auto_approve=auto_approve,
                    security_profile=SecurityProfile(security_profile()),
                    max_steps=40,
                    live=False,
                    platform="babysit",
                    loop_mode="full",
                    agent_mode="goal",
                )
            )
        finally:
            runtime.close()
        await _sleep(15)

    return BabysitResult(
        pr=str(pr),
        ok=False,
        rounds=rounds,
        status="stuck",
        message="Max babysit rounds reached without green checks.",
        checks=checks_history,
    )


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
