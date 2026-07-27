"""tools.yaml allow/deny policy + sandbox wrap."""

from __future__ import annotations

from pathlib import Path

from kageha.harness.shell_sandbox import wrap_shell_command
from kageha.harness.tool_policy import (
    expand_policy_entry,
    filter_tool_names,
    tool_denied,
)


def test_expand_group_runtime():
    assert "bash" in expand_policy_entry("group:runtime")


def test_deny_wins(tmp_path: Path, monkeypatch):
    policy = {"allow": ["bash", "read_file"], "deny": ["bash"]}
    assert tool_denied("bash", policy=policy)
    assert not tool_denied("read_file", policy=policy)
    assert tool_denied("web_search", policy=policy)  # outside allow


def test_filter_tool_names():
    names = filter_tool_names(
        ["bash", "read_file", "web_search"],
        policy={"deny": ["group:runtime"]},
    )
    assert "bash" not in names
    assert "read_file" in names


def test_seatbelt_wrap_when_available(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_SANDBOX", "seatbelt")
    cmd, cleanup = wrap_shell_command("echo hi", tmp_path, allow_network=False)
    # On machines without sandbox-exec, wrap is a no-op.
    import shutil

    if shutil.which("sandbox-exec"):
        assert "sandbox-exec" in cmd
        assert cleanup is not None
        if cleanup:
            cleanup.unlink(missing_ok=True)
    else:
        assert cmd == "echo hi"


def test_sandbox_off(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_SANDBOX", "off")
    cmd, cleanup = wrap_shell_command("ls", tmp_path)
    assert cmd == "ls"
    assert cleanup is None


def test_home_deny_not_wiped_by_empty_repo_stub(tmp_path: Path, monkeypatch):
    """~/.kageha/tools.yaml deny must survive repo tools.yaml with deny: []."""
    from kageha.harness.tool_policy import load_tools_policy

    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    (tmp_path / "tools.yaml").write_text("tools:\n  deny: [group:browser]\n")
    # Point project stub at an empty-deny file under tmp as well via cwd overlay.
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "tools.yaml").write_text("tools:\n  allow: []\n  deny: []\n")
    monkeypatch.chdir(proj)
    # Also ensure kageha_home tools is loaded; load_tools_policy uses tools_policy_paths.
    pol = load_tools_policy()
    assert "group:browser" in (pol.get("deny") or [])
    assert tool_denied("browser_open", policy=pol)
