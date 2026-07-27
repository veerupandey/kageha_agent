"""OS sandbox wrap depth (OpenClaw-aligned docker/bwrap/seatbelt)."""

from __future__ import annotations

import platform
import shutil
from pathlib import Path

import pytest

from kageha.harness.shell_sandbox import (
    docker_network_mode,
    sandbox_status,
    wrap_shell_command,
    workspace_access,
)


def test_sandbox_off(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_SANDBOX", "off")
    cmd, cleanup = wrap_shell_command("ls", tmp_path)
    assert cmd == "ls"
    assert cleanup is None


def test_elevated_skips_wrap(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_SANDBOX", "docker")
    cmd, cleanup = wrap_shell_command("echo hi", tmp_path, elevated=True)
    assert cmd == "echo hi"
    assert cleanup is None


def test_docker_wrap_hardened(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_SANDBOX", "docker")
    monkeypatch.delenv("KAGEHA_SANDBOX_DOCKER_BINDS", raising=False)
    import shutil

    if not shutil.which("docker"):
        cmd, _ = wrap_shell_command("echo hi", tmp_path, allow_network=False)
        assert cmd == "echo hi"
        return
    cmd, cleanup = wrap_shell_command("echo hi", tmp_path, allow_network=False)
    assert cleanup is None
    assert "docker" in cmd
    assert "--network=none" in cmd
    assert "--cap-drop=ALL" in cmd
    assert "--read-only" in cmd
    assert "no-new-privileges" in cmd
    assert f"{tmp_path.resolve()}:/work:rw" in cmd or ":/work:rw" in cmd


def test_docker_blocks_host_network(monkeypatch):
    monkeypatch.setenv("KAGEHA_SANDBOX_DOCKER_NETWORK", "host")
    assert docker_network_mode(allow_network=True) == "bridge"
    monkeypatch.setenv("KAGEHA_SANDBOX_DOCKER_NETWORK", "container:abc")
    assert docker_network_mode(allow_network=True) == "bridge"
    monkeypatch.setenv("KAGEHA_SANDBOX_DOCKER_NETWORK", "bridge")
    assert docker_network_mode(allow_network=True) == "bridge"
    assert docker_network_mode(allow_network=False) == "none"


def test_docker_bind_denylist(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_SANDBOX", "docker")
    monkeypatch.setenv(
        "KAGEHA_SANDBOX_DOCKER_BINDS",
        "/etc/passwd:/etc/passwd:ro,/var/run/docker.sock:/var/run/docker.sock",
    )
    import shutil

    if not shutil.which("docker"):
        return
    cmd, _ = wrap_shell_command("true", tmp_path)
    assert "/etc/passwd" not in cmd
    assert "docker.sock" not in cmd


def test_workspace_access_ro(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_SANDBOX_WORKSPACE_ACCESS", "ro")
    assert workspace_access() == "ro"
    monkeypatch.setenv("KAGEHA_SANDBOX", "docker")
    import shutil

    if not shutil.which("docker"):
        return
    cmd, _ = wrap_shell_command("true", tmp_path)
    assert ":/work:ro" in cmd


def test_bwrap_wrap_when_available(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_SANDBOX", "bwrap")
    import shutil

    cmd, cleanup = wrap_shell_command("echo hi", tmp_path, allow_network=False)
    if shutil.which("bwrap"):
        assert "bwrap" in cmd
        assert "--unshare-net" in cmd
        assert cleanup is None
    else:
        assert cmd == "echo hi"


def test_sandbox_status_smoke(monkeypatch):
    monkeypatch.setenv("KAGEHA_SANDBOX", "off")
    st = sandbox_status()
    assert st.profile == "off"
    assert st.available


def test_ssh_wrap_requires_host(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_SANDBOX", "ssh")
    monkeypatch.delenv("KAGEHA_SANDBOX_SSH_HOST", raising=False)
    cmd, cleanup = wrap_shell_command("echo hi", tmp_path)
    assert "ERROR:" in cmd and "exit 78" in cmd
    assert cleanup is None


def test_ssh_wrap_shape(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_SANDBOX", "ssh")
    monkeypatch.setenv("KAGEHA_SANDBOX_SSH_HOST", "example.test")
    monkeypatch.setenv("KAGEHA_SANDBOX_SSH_USER", "agent")
    monkeypatch.delenv("KAGEHA_SANDBOX_SSH_SYNC", raising=False)
    import shutil

    if not shutil.which("ssh"):
        return
    cmd, cleanup = wrap_shell_command("echo hi", tmp_path)
    assert cleanup is None
    assert "ssh" in cmd
    assert "BatchMode=yes" in cmd
    assert "agent@example.test" in cmd
    assert "tar -c" in cmd
    # bidirectional: push then pull
    assert " && " in cmd


def test_ssh_wrap_push_only(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_SANDBOX", "ssh")
    monkeypatch.setenv("KAGEHA_SANDBOX_SSH_HOST", "example.test")
    monkeypatch.setenv("KAGEHA_SANDBOX_SSH_SYNC", "push")
    import shutil

    if not shutil.which("ssh"):
        return
    cmd, _ = wrap_shell_command("echo hi", tmp_path)
    assert "tar -c" in cmd
    assert cmd.count("tar -c") == 1  # push only, no pull tar from remote


def test_seatbelt_wrap_when_available(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_SANDBOX", "seatbelt")
    import shutil

    cmd, cleanup = wrap_shell_command("echo hi", tmp_path, allow_network=False)
    if shutil.which("sandbox-exec"):
        assert "sandbox-exec" in cmd
        assert cleanup is not None
        text = cleanup.read_text()
        assert "deny network" in text
        assert str(tmp_path.resolve()) in text
        # Host /tmp must not be a blanket write allow (KAGEHA_HOME-under-/tmp escape).
        assert '(subpath "/tmp")' not in text
        assert '(subpath "/private/tmp")' not in text
        assert ".kageha-tmp" in text or str(tmp_path.resolve()) in text
        cleanup.unlink(missing_ok=True)
    else:
        assert cmd == "echo hi"


@pytest.mark.skipif(
    platform.system() != "Darwin" or not shutil.which("sandbox-exec"),
    reason="macOS seatbelt only",
)
def test_seatbelt_blocks_write_outside_coding_root(tmp_path: Path, monkeypatch):
    """Regression: bash must not write siblings under /tmp via host /tmp grant."""
    import asyncio

    monkeypatch.setenv("KAGEHA_SANDBOX", "seatbelt")
    monkeypatch.setenv("KAGEHA_SECURITY_PROFILE", "strict")

    home = tmp_path / "kageha-home"
    session = home / "sessions" / "run1"
    session.mkdir(parents=True)
    outside = home / "escaped.txt"

    from kageha.harness.sandbox import run_shell

    result = asyncio.run(
        run_shell(
            f"echo pwned > {outside}",
            session,
            timeout=10,
            allow_network=False,
        )
    )
    assert not outside.is_file(), (
        f"sandbox escape: wrote {outside} (exit={result.exit_code} "
        f"stderr={result.stderr!r})"
    )
    # In-root write still works.
    inside = session / "ok.txt"
    ok = asyncio.run(
        run_shell(
            "echo hi > ok.txt",
            session,
            timeout=10,
            allow_network=False,
        )
    )
    assert ok.exit_code == 0, ok.stderr
    assert inside.is_file()
    assert inside.read_text().strip() == "hi"
    assert (session / ".kageha-tmp").is_dir()
