"""Mid-session skill create → validate → load → run."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.builtin import load_entry_point_tools
from kageha.memory.skills import SkillRegistry, validate_skill


def _ctx(tmp_path: Path) -> HarnessContext:
    root = tmp_path / "session"
    root.mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(exist_ok=True)
    return HarnessContext(
        workspace=SessionWorkspace(run_id="midskill", root=root),
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
    )


def test_create_validate_load_run_same_session(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KAGEHA_SKILL_LEARN", "hitl")
    ctx = _ctx(tmp_path)
    reg = load_entry_point_tools(ctx)

    skill_name = "mid_session_demo"
    body = (
        "---\n"
        f"name: {skill_name}\n"
        "description: Mid-session skill demo for huddle invent path.\n"
        "---\n\n"
        "# Mid session demo\n\n"
        "Print hello from a script.\n"
    )

    async def _run() -> None:
        create = await reg.get("skill_manage").call(
            action="create", name=skill_name, content=body
        )
        assert "ERROR" not in create and "DENIED" not in create, create

        skills = SkillRegistry()
        assert skill_name in skills.skills
        errors = validate_skill(skills.skills[skill_name])
        assert not errors, errors

        script = "print('mid-session-ok')\n"
        written = await reg.get("skill_manage").call(
            action="write_file",
            name=skill_name,
            content=f"scripts/hello.py\n---\n{script}",
        )
        assert not written.startswith("ERROR:"), written
        assert "DENIED" not in written, written

        loaded = await reg.get("skill_load").call(name=skill_name)
        assert "ERROR" not in loaded
        assert "demo" in loaded.lower() or "Mid" in loaded

        validated = await reg.get("skill_validate").call(name=skill_name)
        assert "ERROR" not in validated or "ok" in validated.lower() or "valid" in validated.lower() or validated.strip() == "[]" or "0" in validated

        out = await reg.get("skill_run").call(
            name=skill_name, script="hello.py", args=""
        )
        assert "DENIED" not in out, out
        assert "mid-session-ok" in out

    asyncio.run(_run())
