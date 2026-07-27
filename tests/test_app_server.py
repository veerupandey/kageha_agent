import asyncio

from kageha.app_server import AppServer
from kageha.harness.approvals import ApprovalGate, ApprovalRequest


def test_app_server_ping():
    async def _run():
        s = AppServer()
        resp = await s.handle({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}})
        assert resp["result"]["pong"] is True

    asyncio.run(_run())


def test_web_approver_surfaces_pending_and_resolves():
    """Plan Build / request_approval share this waiter + thread/approve path."""

    async def _run():
        s = AppServer()
        thread_id = "web-plan-1"
        decisions: list[str] = []

        def audit(req: ApprovalRequest, decision: str) -> None:
            decisions.append(decision)
            if decision == "pending":
                setattr(req, "approval_id", "shared-aid-1")

        approver = s._make_web_approver(thread_id)
        gate = ApprovalGate(auto_approve=True, approver=approver, audit=audit)

        async def ask():
            return await gate.require_explicit(
                ApprovalRequest(
                    action="approve_plan",
                    detail="plan ready",
                    risk_class="plan",
                )
            )

        task = asyncio.create_task(ask())
        for _ in range(50):
            pending = (s.threads.get(thread_id) or {}).get("pending_approval")
            if isinstance(pending, dict) and pending.get("approval_id"):
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("pending_approval never published")

        assert pending["approval_id"] == "shared-aid-1"
        assert pending["action"] == "approve_plan"
        assert pending["risk_class"] == "plan"

        resp = await s.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "thread/approve",
                "params": {
                    "approval_id": "shared-aid-1",
                    "approved": True,
                },
            }
        )
        assert resp["result"]["ok"] is True
        assert await task is True
        assert "pending" in decisions
        assert "approved" in decisions
        assert (s.threads.get(thread_id) or {}).get("pending_approval") is None

    asyncio.run(_run())
