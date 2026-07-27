import asyncio
import json

from kageha.harness.approvals import ApprovalDecision, ApprovalGate, ApprovalRequest
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.builtin import register as register_builtin


def test_shell_classification():
    gate = ApprovalGate(auto_approve=True)
    assert gate.classify_shell("ls -la") == ApprovalDecision.AUTO
    assert gate.classify_shell("pip install cowsay") == ApprovalDecision.ASK
    assert gate.classify_shell("uv sync --extra browser") == ApprovalDecision.ASK
    assert gate.classify_shell("uv add httpx") == ApprovalDecision.ASK
    assert gate.classify_shell("uv tool install kageha") == ApprovalDecision.ASK
    assert gate.classify_shell("sudo rm -rf /") == ApprovalDecision.ASK


def test_fail_closed_without_approver():
    gate = ApprovalGate(auto_approve=False, approver=None)

    async def _run():
        ok = await gate.require(
            ApprovalRequest(
                action="test",
                detail="x",
                risk_class="t",
                default=ApprovalDecision.ASK,
            )
        )
        assert ok is False

    asyncio.run(_run())


def test_require_explicit_ignores_auto_approve():
    """Plan Build / request_approval must ask even when tool auto_approve is on."""
    decisions: list[str] = []

    async def approver(req: ApprovalRequest) -> bool:
        assert req.action == "approve_plan"
        assert getattr(req, "approval_id", None)
        return True

    def audit(req: ApprovalRequest, decision: str) -> None:
        decisions.append(decision)
        if decision == "pending":
            setattr(req, "approval_id", "aid-plan-1")

    gate = ApprovalGate(auto_approve=True, approver=approver, audit=audit)

    async def _run():
        # Normal require would short-circuit on auto_approve.
        auto = await gate.require(
            ApprovalRequest(
                action="bash",
                detail="ls",
                risk_class="shell",
                default=ApprovalDecision.ASK,
            )
        )
        assert auto is True
        assert "approved_auto" in decisions

        ok = await gate.require_explicit(
            ApprovalRequest(
                action="approve_plan",
                detail="plan.md preview",
                risk_class="plan",
            )
        )
        assert ok is True
        assert "pending" in decisions
        assert "approved" in decisions

    asyncio.run(_run())


def test_require_explicit_fail_closed_without_approver():
    gate = ApprovalGate(auto_approve=True, approver=None)

    async def _run():
        ok = await gate.require_explicit(
            ApprovalRequest(
                action="approve_plan",
                detail="x",
                risk_class="plan",
            )
        )
        assert ok is False

    asyncio.run(_run())


def test_request_approval_tool_uses_explicit_bus(tmp_path):
    async def approver(req: ApprovalRequest) -> bool:
        # Default risk_class is plan (allowlisted HITL gate), not a free-form label.
        assert req.risk_class == "plan"
        return False

    def audit(req: ApprovalRequest, decision: str) -> None:
        if decision == "pending":
            setattr(req, "approval_id", "aid-tool-1")

    ws = SessionWorkspace(run_id="test-req-approval", root=tmp_path / "ws")
    (ws.root).mkdir(parents=True, exist_ok=True)
    gate = ApprovalGate(auto_approve=True, approver=approver, audit=audit)
    ctx = HarnessContext(workspace=ws, approvals=gate, router=None)  # type: ignore[arg-type]
    reg = register_builtin(ctx)
    tool = reg.get("request_approval")
    assert tool is not None

    async def _run():
        out = await tool.call(reason="Ship to prod?")
        data = json.loads(out)
        assert data["status"] == "denied"
        assert data["approved"] is False

    asyncio.run(_run())
