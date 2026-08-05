import { describe, expect, it } from "vitest";

import { pendingApprovalFromFrame, terminalRunState } from "./reattach";

describe("Codex-style reconnect state", () => {
  it("restores an approval from replayed event history", () => {
    expect(
      pendingApprovalFromFrame(
        {
          kind: "approval_required",
          label: "Waiting for approval…",
          payload: {
            approval_id: "approval-1",
            action: "bash_elevated",
            risk_class: "shell_elevated",
            detail: "Run outside the sandbox",
          },
        },
        "session-1",
      ),
    ).toEqual({
      approval_id: "approval-1",
      sessionId: "session-1",
      action: "bash_elevated",
      risk_class: "shell_elevated",
      detail: "Run outside the sandbox",
      label: "Waiting for approval…",
    });
  });

  it("does not present an interrupted turn as successful", () => {
    expect(terminalRunState("reconciliation_required")).toEqual({
      status: "interrupted",
      label: "Interrupted · review before resuming",
    });
    expect(terminalRunState("blocked").status).toBe("interrupted");
    expect(terminalRunState("complete").status).toBe("success");
  });
});
