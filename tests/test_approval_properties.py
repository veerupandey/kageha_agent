"""Property-based tests for ApprovalGate behavior (REL-001).

Validates approval gate properties using Hypothesis.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

from kageha.harness.approvals import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalOutcome,
    ApprovalRequest,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_ACTIONS = st.sampled_from(["bash", "shell", "tool:forge", "tool:browser", "tool:computer"])
_RISK_CLASSES = st.sampled_from(["destructive", "network", "filesystem", "general"])


@st.composite
def _unique_approval_requests(draw: st.DrawFn, min_size: int = 1, max_size: int = 10):
    """Generate a list of approval requests with unique action+detail combos.

    Each request has a unique detail so allowlist caching doesn't interfere
    with testing whether the injected approver is consulted for each request.
    """
    count = draw(st.integers(min_value=min_size, max_value=max_size))
    requests = []
    for i in range(count):
        action = draw(_ACTIONS)
        # Use unique detail per request to avoid allowlist collisions
        detail = f"unique_action_{i}_{draw(st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=('L', 'N'))))}"
        risk_class = draw(_RISK_CLASSES)
        requests.append(
            ApprovalRequest(
                action=action,
                detail=detail,
                risk_class=risk_class,
                default=ApprovalDecision.ASK,
            )
        )
    return requests


_approval_decisions = st.booleans()


@st.composite
def _approval_requests_for_deny(draw: st.DrawFn, min_size: int = 1, max_size: int = 10):
    """Generate a list of approval requests that should be denied by a fail-closed gate.

    These requests use ApprovalDecision.ASK (the default for mutating tool calls)
    to exercise the path where the gate must consult an approver — which, when
    absent, means denial.
    """
    count = draw(st.integers(min_value=min_size, max_value=max_size))
    requests = []
    for i in range(count):
        action = draw(_ACTIONS)
        detail = draw(st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N", "P"))))
        risk_class = draw(_RISK_CLASSES)
        requests.append(
            ApprovalRequest(
                action=action,
                detail=f"mutating_{i}_{detail}",
                risk_class=risk_class,
                default=ApprovalDecision.ASK,
            )
        )
    return requests


# ---------------------------------------------------------------------------
# Property 5: Mutating tool calls fail closed by default, regardless of
# construction path
# ---------------------------------------------------------------------------


@given(requests=_approval_requests_for_deny(min_size=1, max_size=10))
@settings(max_examples=100)
def test_fail_closed_default_construction(
    requests: list[ApprovalRequest],
) -> None:
    """**Validates: Requirements 2.1, 2.3**

    For any ApprovalGate constructed without an explicit approver (approver=None)
    and with auto_approve=False, all mutating tool call requests are denied.
    The gate returns DENIED before the driver is reached — this holds independent
    of how or where the controller was constructed (production wiring or test
    construction).
    """
    # Construct gate exactly as LoopController does when no approver is supplied:
    # approver=None, auto_approve=False — the fail-closed default.
    gate = ApprovalGate(
        approver=None,
        auto_approve=False,
    )
    # Clear the allowlist so no prior state can interfere
    gate._allowlist = set()

    audit_log: list[tuple[ApprovalRequest, str]] = []

    def _audit(req: ApprovalRequest, label: str) -> None:
        audit_log.append((req, label))

    gate.audit = _audit

    async def _run() -> None:
        for i, req in enumerate(requests):
            result = await gate.require(req)
            # Every mutating request must be denied
            assert result is False, (
                f"Request {i} ({req.action}, risk={req.risk_class}): "
                f"expected DENIED (False) but got {result}. "
                f"Gate constructed with approver=None, auto_approve=False should deny all."
            )

    with patch("kageha.harness.approvals._load_allowlist", return_value=set()):
        with patch("kageha.harness.approvals._save_allowlist", lambda _: None):
            asyncio.run(_run())

    # Every request should have been audited as denied_no_approver
    assert len(audit_log) == len(requests), (
        f"Expected {len(requests)} audit entries, got {len(audit_log)}"
    )
    for (req, label) in audit_log:
        assert label == "denied_no_approver", (
            f"Request ({req.action}): expected audit label 'denied_no_approver', got '{label}'. "
            f"When approver is None, denial reason must be 'denied_no_approver'."
        )


# ---------------------------------------------------------------------------
# Property 6: An explicitly injected approver always overrides the
# interactive default
# ---------------------------------------------------------------------------


@given(
    requests=_unique_approval_requests(min_size=1, max_size=10),
    decisions=st.lists(_approval_decisions, min_size=10, max_size=10),
)
@settings(max_examples=100)
def test_injected_approver_overrides_interactive_default(
    requests: list[ApprovalRequest],
    decisions: list[bool],
) -> None:
    """**Validates: Requirements 2.4**

    For any approver object explicitly passed into LoopController/ApprovalGate,
    the ApprovalGate uses that object — never cli_approver — for every approval
    decision in that run. The injected approver's decision is always the outcome.
    """
    # Track which calls the injected approver received
    calls_received: list[ApprovalRequest] = []
    decision_index = 0

    async def injected_approver(req: ApprovalRequest) -> ApprovalOutcome:
        nonlocal decision_index
        calls_received.append(req)
        decision = decisions[decision_index % len(decisions)]
        decision_index += 1
        return ApprovalOutcome(decision)

    # Construct with the explicit approver — this is how LoopController passes
    # the approver through to ApprovalGate
    gate = ApprovalGate(
        approver=injected_approver,
        auto_approve=False,
    )
    # Clear the allowlist so it doesn't interfere with the property being tested
    gate._allowlist = set()

    async def _run() -> None:
        for i, req in enumerate(requests):
            result = await gate.require(req)
            expected_decision = decisions[i % len(decisions)]
            # The injected approver's decision is always the outcome
            assert result == expected_decision, (
                f"Request {i}: expected {expected_decision} from injected approver, got {result}"
            )

    with patch("kageha.harness.approvals._load_allowlist", return_value=set()):
        with patch("kageha.harness.approvals._save_allowlist", lambda _: None):
            asyncio.run(_run())

    # Every request was routed to the injected approver (not cli_approver)
    assert len(calls_received) == len(requests), (
        f"Expected {len(requests)} calls to injected approver, got {len(calls_received)}. "
        "Some requests may have been handled by cli_approver or another path."
    )
    for original, received in zip(requests, calls_received):
        assert received.action == original.action
        assert received.detail == original.detail


@given(
    requests=_unique_approval_requests(min_size=1, max_size=10),
)
@settings(max_examples=100)
def test_injected_approver_deny_is_always_final(
    requests: list[ApprovalRequest],
) -> None:
    """**Validates: Requirements 2.4**

    When an injected approver denies every request, the gate always denies.
    The injected approver is the sole decision maker — cli_approver is never
    consulted regardless of construction path.
    """
    calls_received: list[ApprovalRequest] = []

    async def always_deny_approver(req: ApprovalRequest) -> ApprovalOutcome:
        calls_received.append(req)
        return ApprovalOutcome(False)

    gate = ApprovalGate(
        approver=always_deny_approver,
        auto_approve=False,
    )
    gate._allowlist = set()

    async def _run() -> None:
        for i, req in enumerate(requests):
            result = await gate.require(req)
            # Injected approver denied — gate must deny
            assert result is False, (
                f"Request {i}: injected approver denied but gate returned {result}"
            )

    with patch("kageha.harness.approvals._load_allowlist", return_value=set()):
        with patch("kageha.harness.approvals._save_allowlist", lambda _: None):
            asyncio.run(_run())

    # All decisions went through the injected approver
    assert len(calls_received) == len(requests)
