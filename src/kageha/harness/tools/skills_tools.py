"""Full Agent Skills tools — load, run scripts, manage, install."""

from __future__ import annotations

import json
import shlex
from typing import TYPE_CHECKING, Iterable

from kageha.harness.approvals import ApprovalDecision, ApprovalRequest
from kageha.harness.sandbox import run_shell
from kageha.harness.tools.base import ToolRegistry, tool
from kageha.memory.skill_learn import (
    skill_learn_soft_enabled,
    skill_learn_unattended_enabled,
    stamp_unattended_provenance,
)
from kageha.memory.skills import SkillRegistry, validate_skill
from kageha.memory.skills_install import install_skills

if TYPE_CHECKING:
    from kageha.harness.runtime import HarnessContext


def activate_skills(
    ctx: "HarnessContext",
    names: Iterable[str],
    *,
    registry: SkillRegistry | None = None,
) -> list[str]:
    """Mark skills active and union allowed-tools into ctx.meta."""
    skills = registry or SkillRegistry()
    active = list(ctx.meta.get("active_skills") or [])
    for name in names:
        n = (name or "").strip()
        if n and n not in active and skills.get(n):
            active.append(n)
    ctx.meta["active_skills"] = active
    allowed: set[str] = set()
    restrictors = 0
    open_skills = 0
    for n in active:
        sk = skills.get(n)
        if not sk:
            continue
        if sk.allowed_tools:
            restrictors += 1
            allowed.update(sk.allowed_tools)
        else:
            open_skills += 1
    # Only narrow the catalog when every active skill declares allowed-tools.
    # An open skill (no allowlist) means "don't globally strip the catalog".
    # When mixed, still union declared allowlists so restricted skills keep their tools.
    if restrictors and open_skills == 0:
        ctx.meta["skill_allowed_tools"] = sorted(allowed)
    elif restrictors and open_skills:
        # Mixed: keep union of declared allowlists (does not strip unrelated core tools
        # beyond _filter_tools_for_skills keep_prefixes).
        ctx.meta["skill_allowed_tools"] = sorted(allowed)
    else:
        ctx.meta["skill_allowed_tools"] = None
    return active


def register_skills_tools(ctx: "HarnessContext") -> ToolRegistry:
    reg = ToolRegistry()
    skills = SkillRegistry()

    def _activate(name: str) -> None:
        activate_skills(ctx, [name], registry=skills)

    def _interactive() -> bool | None:
        flag = ctx.meta.get("skill_learn_interactive")
        if flag is None:
            return None
        return bool(flag)

    @tool(description="List installed Agent Skills (name + description).")
    async def skill_list(query: str = "") -> str:
        return skills.catalog(query=query or None)

    @tool(
        description=(
            "Load a skill body into context (L2 progressive disclosure). "
            "Marks the skill active for allowed-tools enforcement."
        )
    )
    async def skill_load(name: str) -> str:
        body = skills.load_body(name)
        if not body.startswith("ERROR:"):
            _activate(name)
        return body

    @tool(
        description=(
            "List files under a skill's scripts/, references/, assets/, templates/ (L3)."
        )
    )
    async def skill_list_resources(name: str) -> str:
        sk = skills.get(name)
        if not sk:
            return f"ERROR: unknown skill {name}"
        res = sk.list_resources()
        return "\n".join(res) if res else "(no resources)"

    @tool(
        description=(
            "Read a skill resource file (L3). "
            "path like references/forms.md or scripts/extract.py"
        )
    )
    async def skill_read(name: str, path: str) -> str:
        return skills.read_resource(name, path)

    @tool(
        description=(
            "Run a script from a skill's scripts/ directory in a sandboxed shell. "
            "script is relative (e.g. extract.py or scripts/extract.py). "
            "args is a shell argument string. "
            "Runs with cwd = session workspace so --workspace . is safe."
        ),
        risk_class="skill",
    )
    async def skill_run(name: str, script: str, args: str = "") -> str:
        resolved = skills.resolve_script(name, script)
        if isinstance(resolved, str):
            return resolved
        ok = await ctx.approvals.require(
            ApprovalRequest(
                action="skill_run",
                detail=f"{name}: {resolved.name} {args}".strip(),
                risk_class="skill",
                default=ApprovalDecision.ASK,
            )
        )
        if not ok:
            return "DENIED: skill_run not approved"
        _activate(name)
        cmd = f"python {shlex.quote(str(resolved))}"
        if args.strip():
            cmd = f"{cmd} {args}"
        work_cwd = ctx.workspace.root
        result = await run_shell(
            cmd,
            cwd=work_cwd,
            env={"KAGEHA_WORKSPACE": str(work_cwd)},
            allow_network=True,
            timeout=300.0,
        )
        return json.dumps(
            {
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "cwd": str(work_cwd),
                "script": str(resolved.relative_to(skills.get(name).path)),  # type: ignore[union-attr]
            }
        )

    @tool(
        description=(
            "Manage a skill (agentskills.io SKILL.md). "
            "Actions: create, edit, patch, delete, observe, refine, write_file, "
            "list_resources, load, list. "
            "Lifecycle: create → observe (pitfalls) → refine (improve steps). "
            "KAGEHA_SKILL_LEARN=soft|unattended|hitl|off "
            "(unattended auto-approves create/edit/patch/write_file on TTY; "
            "delete always HITL)."
        ),
        risk_class="skill",
    )
    async def skill_manage(action: str, name: str, content: str = "") -> str:
        action = (action or "").strip()
        soft_actions = {"observe", "refine"}
        # delete is never auto-approved — even in unattended mode.
        unattended_mutators = {"create", "edit", "patch", "write_file"}
        hard_mutators = {"create", "edit", "patch", "delete", "write_file"}

        def _record_soft(act: str, result: str) -> None:
            payload = {
                "action": act,
                "name": name,
                "result": result[:200],
            }
            ctx.meta["skill_learn_soft_last"] = payload
            hist = list(ctx.meta.get("skill_learn_soft_events") or [])
            hist.append(payload)
            ctx.meta["skill_learn_soft_events"] = hist[-20:]
            emitter = ctx.meta.get("events")
            if emitter is not None and hasattr(emitter, "emit"):
                try:
                    emitter.emit("skill_learn_soft", payload)
                except Exception:  # noqa: BLE001
                    pass

        if action in soft_actions:
            soft = skill_learn_soft_enabled(interactive=_interactive())
            if soft:
                out = skills.manage(action, name, content, approved=True)
                _record_soft(action, out)
                return out
            ok = await ctx.approvals.require(
                ApprovalRequest(
                    action=f"skill_manage:{action}",
                    detail=f"{name}\n{content[:500]}",
                    risk_class="skill_write",
                    default=ApprovalDecision.ASK,
                )
            )
            if not ok:
                return "DENIED: skill_manage not approved"
            return skills.manage(action, name, content, approved=True)
        if action in hard_mutators:
            unattended = (
                action in unattended_mutators
                and skill_learn_unattended_enabled(interactive=_interactive())
            )
            if unattended:
                body = content
                if action in {"create", "edit"} and body.strip():
                    body = stamp_unattended_provenance(body)
                out = skills.manage(action, name, body, approved=True)
                _record_soft(action, out)
                return out
            ok = await ctx.approvals.require(
                ApprovalRequest(
                    action=f"skill_manage:{action}",
                    detail=f"{name}\n{content[:500]}",
                    risk_class="skill_write",
                    default=ApprovalDecision.ASK,
                )
            )
            if not ok:
                return "DENIED: skill_manage not approved"
            return skills.manage(action, name, content, approved=True)
        if action == "load":
            body = skills.load_body(name)
            if not body.startswith("ERROR:"):
                _activate(name)
            return body
        if action == "list":
            return skills.catalog()
        return skills.manage(action, name, content, approved=False)

    @tool(
        description=(
            "Install Agent Skills from a local path or GitHub "
            "(e.g. anthropics/skills/pdf). Requires approval."
        ),
        risk_class="skill_write",
    )
    async def skill_install(spec: str, only: str = "", force: bool = False) -> str:
        ok = await ctx.approvals.require(
            ApprovalRequest(
                action="skill_install",
                detail=f"spec={spec} only={only} force={force}",
                risk_class="skill_write",
                default=ApprovalDecision.ASK,
            )
        )
        if not ok:
            return "DENIED: skill_install not approved"
        only_list = [x.strip() for x in only.split(",") if x.strip()] or None
        try:
            result = install_skills(spec, only=only_list, force=force, registry=skills)
        except Exception as e:  # noqa: BLE001
            return f"ERROR: {e}"
        skills.reload()
        return json.dumps(
            {
                "installed": result.installed,
                "skipped": result.skipped,
                "source": result.source,
                "dest": result.dest_root,
            }
        )

    @tool(description="Validate a skill against agentskills.io rules.")
    async def skill_validate(name: str = "") -> str:
        skills.reload()
        if name:
            sk = skills.get(name)
            if not sk:
                return f"ERROR: unknown skill {name}"
            errs = validate_skill(sk)
            return json.dumps({"name": sk.name, "ok": not errs, "errors": errs})
        rows = []
        for sk in skills.skills.values():
            errs = validate_skill(sk)
            rows.append({"name": sk.name, "ok": not errs, "errors": errs})
        return json.dumps(rows, indent=2)

    for t in (
        skill_list,
        skill_load,
        skill_list_resources,
        skill_read,
        skill_run,
        skill_manage,
        skill_install,
        skill_validate,
    ):
        if hasattr(t, "name"):
            reg.register(t)  # type: ignore[arg-type]
    return reg
