import asyncio
import json

from kageha.harness.approvals import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    apply_permission_scope,
    process_permissions,
)
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.builtin import register as register_builtin


def test_shell_classification():
    gate = ApprovalGate(auto_approve=True)
    assert gate.classify_shell("ls -la") == ApprovalDecision.AUTO
    assert gate.classify_shell("pip install cowsay") == ApprovalDecision.ASK
    assert gate.classify_shell("pip3 install cowsay") == ApprovalDecision.ASK
    assert gate.classify_shell("python -m pip install httpx") == ApprovalDecision.ASK
    assert gate.classify_shell("uv pip install pillow") == ApprovalDecision.ASK
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
        assert ok.approved is True
        assert "pending" in decisions
        assert "approved" in decisions

    asyncio.run(_run())


def test_require_explicit_suggest_sets_feedback():
    async def approver(_req: ApprovalRequest):
        from kageha.harness.approvals import ApprovalOutcome

        return ApprovalOutcome(False, feedback="prefer Redis")

    gate = ApprovalGate(auto_approve=True, approver=approver)

    async def _run():
        ok = await gate.require_explicit(
            ApprovalRequest(
                action="approve_plan",
                detail="plan",
                risk_class="plan",
            )
        )
        assert ok.approved is False
        assert ok.feedback == "prefer Redis"
        assert gate.last_feedback == "prefer Redis"
        assert "Redis" in gate.denial_message("plan")

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
        assert ok.approved is False

    asyncio.run(_run())


def test_full_access_allows_host_escape_without_prompt(monkeypatch):
    import kageha.harness.approvals as ap

    monkeypatch.setattr(
        ap,
        "_PROCESS_PERMISSIONS",
        {"auto_approve": True, "sandbox_network": True, "scope": "full"},
    )
    decisions: list[str] = []
    gate = ApprovalGate(
        approver=None,
        audit=lambda _req, decision: decisions.append(decision),
    )

    async def _run():
        outcome = await gate.require_host_escape(
            ApprovalRequest(
                action="bash_elevated",
                detail="test -f /tmp/example",
                risk_class="shell_elevated",
            )
        )
        assert outcome.approved is True
        assert outcome.scope == "full"

    asyncio.run(_run())
    assert decisions == ["approved_full_access"]


def test_downgrading_full_access_to_auto_restores_sandbox_boundary(monkeypatch):
    import kageha.harness.approvals as ap

    monkeypatch.setattr(
        ap,
        "_PROCESS_PERMISSIONS",
        {"auto_approve": False, "sandbox_network": False, "scope": "ask"},
    )
    monkeypatch.delenv("KAGEHA_SANDBOX_ALLOW_NETWORK", raising=False)

    apply_permission_scope("full")
    assert process_permissions()["sandbox_network"] is True

    result = apply_permission_scope("session")
    assert result["scope"] == "session"
    assert process_permissions()["sandbox_network"] is False
    assert "KAGEHA_SANDBOX_ALLOW_NETWORK" not in ap.os.environ


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


def test_injected_approver_used_for_all_decisions(monkeypatch):
    """An explicitly injected approver is used for all decisions in that run (REL-001 / Req 2.4)."""
    import kageha.harness.approvals as ap

    # Ensure no disk allowlist interferes
    monkeypatch.setattr(ap, "_load_allowlist", lambda: set())
    monkeypatch.setattr(ap, "_save_allowlist", lambda _entries: None)

    calls: list[ApprovalRequest] = []

    async def custom_approver(req: ApprovalRequest) -> bool:
        calls.append(req)
        return True

    # Construct gate with an explicit approver (no cli_approver fallback)
    gate = ApprovalGate(auto_approve=False, approver=custom_approver)
    gate._allowlist = set()  # Ensure empty allowlist

    async def _run():
        # First decision
        ok = await gate.require(
            ApprovalRequest(
                action="bash",
                detail="rm -rf /tmp/foo",
                risk_class="destructive",
                default=ApprovalDecision.ASK,
            )
        )
        assert ok is True
        assert len(calls) == 1
        assert calls[0].action == "bash"

        # Second decision — same run, still uses injected approver
        ok2 = await gate.require(
            ApprovalRequest(
                action="tool:forge",
                detail="requests.get(...)",
                risk_class="network",
                default=ApprovalDecision.ASK,
            )
        )
        assert ok2 is True
        assert len(calls) == 2
        assert calls[1].action == "tool:forge"

    asyncio.run(_run())


def test_injected_approver_deny_propagates(monkeypatch):
    """When the injected approver denies, the gate denies (Req 2.4)."""
    import kageha.harness.approvals as ap

    monkeypatch.setattr(ap, "_load_allowlist", lambda: set())
    monkeypatch.setattr(ap, "_save_allowlist", lambda _entries: None)

    async def deny_approver(_req: ApprovalRequest) -> bool:
        return False

    gate = ApprovalGate(auto_approve=False, approver=deny_approver)
    gate._allowlist = set()

    async def _run():
        ok = await gate.require(
            ApprovalRequest(
                action="bash",
                detail="curl evil.com",
                risk_class="network",
                default=ApprovalDecision.ASK,
            )
        )
        assert ok is False

    asyncio.run(_run())


def test_allowlist_approves_without_approver_or_auto_approve(monkeypatch):
    """Allowlist-matched actions are approved regardless of auto_approve or approver (REL-001 / Req 2.5)."""
    import kageha.harness.approvals as ap

    # Reset process permissions so auto_approve is off
    monkeypatch.setattr(ap, "_PROCESS_PERMISSIONS", {
        "auto_approve": False,
        "sandbox_network": False,
        "scope": "ask",
    })

    # Gate with NO approver and auto_approve=False
    gate = ApprovalGate(auto_approve=False, approver=None)

    # Manually inject an allowlist entry
    req = ApprovalRequest(
        action="bash",
        detail="ls -la",
        risk_class="shell",
        default=ApprovalDecision.ASK,
    )
    key = f"{req.action}|{req.risk_class}|{req.detail.strip()}"
    gate._allowlist = {key}

    audit_log: list[str] = []
    gate.audit = lambda _req, decision: audit_log.append(decision)

    async def _run():
        ok = await gate.require(req)
        assert ok is True
        assert "approved_allowlist" in audit_log

    asyncio.run(_run())


def test_allowlist_approves_even_with_auto_approve_off_and_approver_present(monkeypatch):
    """Allowlist match short-circuits before reaching the approver (Req 2.5)."""
    import kageha.harness.approvals as ap

    monkeypatch.setattr(ap, "_PROCESS_PERMISSIONS", {
        "auto_approve": False,
        "sandbox_network": False,
        "scope": "ask",
    })

    approver_called = []

    async def spy_approver(req: ApprovalRequest) -> bool:
        approver_called.append(req)
        return False  # would deny if reached

    gate = ApprovalGate(auto_approve=False, approver=spy_approver)

    req = ApprovalRequest(
        action="bash",
        detail="pip install cowsay",
        risk_class="shell_network",
        default=ApprovalDecision.ASK,
    )
    key = f"{req.action}|{req.risk_class}|{req.detail.strip()}"
    gate._allowlist = {key}

    async def _run():
        ok = await gate.require(req)
        assert ok is True
        # The approver should never have been called — allowlist short-circuits
        assert approver_called == []

    asyncio.run(_run())


def test_only_cli_entrypoint_installs_cli_approver():
    """Only CLI/REPL entrypoints wire cli_approver; WebUI and eval harness do not (REL-001 / Req 2.2)."""
    import ast
    import importlib.util
    from pathlib import Path

    # Locate source files
    src_root = Path(importlib.util.find_spec("kageha").submodule_search_locations[0])
    repl_path = src_root / "chat" / "repl.py"
    app_server_path = src_root / "app_server.py"
    eval_harness_path = src_root / "eval" / "harness.py"

    def _references_cli_approver(source: str) -> bool:
        """Return True if the source code references 'cli_approver' in a meaningful way."""
        tree = ast.parse(source)
        for node in ast.walk(tree):
            # Check import statements
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "cli_approver":
                        return True
            # Check Name references
            if isinstance(node, ast.Name) and node.id == "cli_approver":
                return True
        return False

    # CLI entrypoints SHOULD reference cli_approver (chat REPL wires it)
    assert _references_cli_approver(repl_path.read_text()), (
        "chat/repl.py must import and use cli_approver"
    )

    # WebUI MUST NOT reference cli_approver (it uses _make_web_approver instead)
    assert not _references_cli_approver(app_server_path.read_text()), (
        "app_server.py must NOT import or use cli_approver"
    )

    # Eval harness MUST NOT reference cli_approver (fails closed by default)
    assert not _references_cli_approver(eval_harness_path.read_text()), (
        "eval/harness.py must NOT import or use cli_approver"
    )


def test_eval_harness_does_not_pass_approver():
    """The eval harness TurnRequest omits the approver field, relying on fail-closed default (REL-001 / Req 2.2)."""
    import ast
    import importlib.util
    from pathlib import Path

    src_root = Path(importlib.util.find_spec("kageha").submodule_search_locations[0])
    eval_harness_path = src_root / "eval" / "harness.py"
    source = eval_harness_path.read_text()
    tree = ast.parse(source)

    # Find all TurnRequest(...) calls and check none of them pass 'approver'
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Check if this is a TurnRequest call
            func = node.func
            if isinstance(func, ast.Name) and func.id == "TurnRequest":
                kwarg_names = [kw.arg for kw in node.keywords]
                assert "approver" not in kwarg_names, (
                    "eval/harness.py TurnRequest must NOT pass an approver keyword "
                    "(relies on fail-closed default)"
                )


def test_webui_uses_own_approver_not_cli():
    """The WebUI constructs its own approver via _make_web_approver, never cli_approver (REL-001 / Req 2.2)."""
    import ast
    import importlib.util
    from pathlib import Path

    src_root = Path(importlib.util.find_spec("kageha").submodule_search_locations[0])
    app_server_path = src_root / "app_server.py"
    source = app_server_path.read_text()
    tree = ast.parse(source)

    # Verify _make_web_approver is defined in the module
    has_make_web_approver = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "_make_web_approver":
                has_make_web_approver = True
                break
    assert has_make_web_approver, (
        "app_server.py must define _make_web_approver as its own approver factory"
    )

    # Verify that approver= keyword args reference _make_web_approver, not cli_approver
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "approver":
            # The value should involve _make_web_approver (a method call)
            value_source = ast.dump(node.value)
            assert "cli_approver" not in value_source, (
                "app_server.py must not assign cli_approver to the approver kwarg"
            )


def test_session_and_full_scopes_grant_permissions(monkeypatch):
    import os

    from kageha.harness import approvals as ap
    from kageha.harness.approvals import ApprovalOutcome

    monkeypatch.delenv("KAGEHA_SANDBOX_ALLOW_NETWORK", raising=False)
    ap._PROCESS_PERMISSIONS.update(
        {"auto_approve": False, "sandbox_network": False, "scope": "ask"}
    )
    grants: list[dict] = []

    async def approver(_req: ApprovalRequest) -> ApprovalOutcome:
        return ApprovalOutcome(True, scope="full")

    gate = ApprovalGate(
        auto_approve=False,
        approver=approver,
        on_permission_grant=lambda g: grants.append(g),
    )

    async def _run():
        ok = await gate.require(
            ApprovalRequest(
                action="bash",
                detail="pip install x",
                risk_class="shell_network_or_destructive",
            )
        )
        assert ok is True
        assert gate.auto_approve is True
        assert gate.last_scope == "full"
        assert os.environ.get("KAGEHA_SANDBOX_ALLOW_NETWORK") == "1"
        assert grants and grants[0]["scope"] == "full"
        assert ap.process_permissions()["auto_approve"] is True

    asyncio.run(_run())
