"""OS-level shell isolation profiles: seatbelt (macOS), bwrap (Linux), docker.

Depth goals (OpenClaw-aligned):
- network denied by default
- docker: read-only root, cap-drop ALL, no-new-privileges, tmpfs /tmp
- workspace bind rw|ro
- elevated escape hatch to skip OS wrap (HITL / explicit)
"""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from kageha.config import sandbox_profile


# Paths that must never appear as extra docker binds (OpenClaw-style denylist).
_BLOCKED_BIND_PREFIXES = (
    "/etc",
    "/private/etc",
    "/proc",
    "/sys",
    "/dev",
    "/root",
    "/boot",
    "/run",
    "/var/run",
)


@dataclass(frozen=True)
class SandboxStatus:
    profile: str
    requested: str
    available: bool
    detail: str


def resolve_sandbox_profile() -> str:
    return sandbox_profile()


def workspace_access() -> str:
    """rw (default) or ro for the session workspace mount/write grant."""
    raw = (os.environ.get("KAGEHA_SANDBOX_WORKSPACE_ACCESS") or "rw").strip().lower()
    return "ro" if raw in {"ro", "read", "readonly", "read-only"} else "rw"


def docker_read_only_root() -> bool:
    raw = (os.environ.get("KAGEHA_SANDBOX_READ_ONLY_ROOT") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def docker_network_mode(*, allow_network: bool) -> str:
    """Resolve docker network; block host / container: join."""
    if not allow_network:
        return "none"
    raw = (os.environ.get("KAGEHA_SANDBOX_DOCKER_NETWORK") or "bridge").strip().lower()
    if raw in {"host", "container"} or raw.startswith("container:"):
        # OpenClaw blocks these by default — fail closed to bridge.
        return "bridge"
    if raw in {"", "none"}:
        return "bridge"
    return raw


def sandbox_status() -> SandboxStatus:
    requested = (os.environ.get("KAGEHA_SANDBOX") or "auto").strip() or "auto"
    profile = resolve_sandbox_profile()
    if profile == "off":
        return SandboxStatus(profile, requested, True, "OS wrap disabled (cwd-only)")
    if profile == "seatbelt":
        ok = bool(shutil.which("sandbox-exec"))
        return SandboxStatus(
            profile,
            requested,
            ok,
            "sandbox-exec available" if ok else "sandbox-exec missing — commands run unwrapped",
        )
    if profile == "bwrap":
        ok = bool(shutil.which("bwrap"))
        return SandboxStatus(
            profile,
            requested,
            ok,
            "bwrap available" if ok else "bwrap missing — commands run unwrapped",
        )
    if profile == "docker":
        ok = bool(shutil.which("docker"))
        return SandboxStatus(
            profile,
            requested,
            ok,
            "docker available" if ok else "docker missing — commands run unwrapped",
        )
    if profile == "ssh":
        host = (os.environ.get("KAGEHA_SANDBOX_SSH_HOST") or "").strip()
        ok = bool(shutil.which("ssh") and host)
        detail = (
            f"ssh → {host}"
            if ok
            else (
                "ssh missing or KAGEHA_SANDBOX_SSH_HOST unset — "
                "commands are denied (no silent host unwrap)"
            )
        )
        return SandboxStatus(profile, requested, ok, detail)
    if profile == "modal":
        from kageha.harness.terminal_backend import ModalTerminalBackend

        ok, detail = ModalTerminalBackend().available()
        return SandboxStatus(
            profile,
            requested,
            ok,
            detail if ok else f"{detail} — commands are denied (no silent host unwrap)",
        )
    return SandboxStatus(profile, requested, False, f"unknown profile {profile!r}")


def wrap_shell_command(
    command: str,
    cwd: Path,
    *,
    allow_network: bool = False,
    elevated: bool = False,
    profile: str | None = None,
) -> tuple[str, Path | None]:
    """Return (command_to_run, cleanup_path_or_None).

    When isolation is unavailable or elevated=True, returns the original command.
    """
    if elevated or _env_truthy("KAGEHA_SANDBOX_ELEVATED"):
        return command, None
    mode = (profile or resolve_sandbox_profile()).strip().lower()
    if mode in {"off", "none", "cwd"}:
        return command, None
    if mode == "seatbelt":
        return _wrap_seatbelt(command, cwd, allow_network=allow_network)
    if mode in {"bwrap", "bubblewrap"}:
        return _wrap_bwrap(command, cwd, allow_network=allow_network)
    if mode == "docker":
        return _wrap_docker(command, cwd, allow_network=allow_network)
    if mode == "ssh":
        host = (os.environ.get("KAGEHA_SANDBOX_SSH_HOST") or "").strip()
        if not shutil.which("ssh") or not host:
            # Never silently unwrap to the host when ssh profile was requested.
            return (
                "echo 'ERROR: KAGEHA_SANDBOX=ssh requires ssh + "
                "KAGEHA_SANDBOX_SSH_HOST' >&2; exit 78",
                None,
            )
        return _wrap_ssh(command, cwd, allow_network=allow_network)
    if mode == "modal":
        # Modal uses TerminalBackend.exec — never unwrap to host via shell wrap.
        return (
            "echo 'ERROR: KAGEHA_SANDBOX=modal must run via TerminalBackend' >&2; "
            "exit 78",
            None,
        )
    return command, None


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _shell_quote(command: str) -> str:
    return command.replace("'", "'\\''")


def scratch_dir_for(cwd: Path) -> Path:
    """Per-workspace scratch dir used instead of host ``/tmp`` write grants."""
    path = cwd.resolve() / ".kageha-tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _wrap_seatbelt(
    command: str, cwd: Path, *, allow_network: bool
) -> tuple[str, Path | None]:
    exe = shutil.which("sandbox-exec")
    if not exe:
        return command, None
    root_path = cwd.resolve()
    root = str(root_path)
    # Scratch lives under the coding root so KAGEHA_HOME under /tmp cannot be
    # used as a write escape (blanket /tmp grants previously allowed that).
    scratch = str(scratch_dir_for(root_path))
    access = workspace_access()
    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec)",
        "(allow process-fork)",
        "(allow process-info*)",
        "(allow signal)",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
        "(allow file-read-metadata)",
        # Broad read so interpreters / Homebrew work; writes are confined.
        "(allow file-read*)",
        '(allow file-ioctl (literal "/dev/null") (literal "/dev/zero") '
        '(literal "/dev/dtracehelper"))',
        '(allow file-write* (literal "/dev/null") (literal "/dev/zero"))',
        # Explicitly keep credential dirs non-writable even if nested oddly.
        '(deny file-write* (subpath "/Users") (regex #"^/Users/[^/]+/\\.(ssh|aws|gnupg|docker|config/gcloud)"))',
        '(deny file-write* (subpath "/home") (regex #"^/home/[^/]+/\\.(ssh|aws|gnupg|docker|config/gcloud)"))',
    ]
    if access == "rw":
        lines.append(f'(allow file-write* (subpath "{root}"))')
    else:
        # Read-only workspace: only the in-root scratch dir is writable.
        lines.append(f'(allow file-write* (subpath "{scratch}"))')
    if allow_network:
        lines.append("(allow network*)")
    else:
        lines.append("(deny network*)")
    profile_text = "\n".join(lines) + "\n"
    fd, path = tempfile.mkstemp(prefix="kageha-sb-", suffix=".sb")
    os.close(fd)
    Path(path).write_text(profile_text)
    inner = _shell_quote(command)
    wrapped = f"{exe} -f {shlex.quote(path)} /bin/bash -lc '{inner}'"
    return wrapped, Path(path)


def _wrap_bwrap(
    command: str, cwd: Path, *, allow_network: bool
) -> tuple[str, Path | None]:
    exe = shutil.which("bwrap")
    if not exe:
        return command, None
    root = cwd.resolve()
    access = workspace_access()
    bind = "--bind" if access == "rw" else "--ro-bind"
    parts = [
        exe,
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/bin",
        "/bin",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind-try",
        "/lib64",
        "/lib64",
        "--ro-bind-try",
        "/sbin",
        "/sbin",
        "--ro-bind-try",
        "/etc",
        "/etc",
        "--ro-bind-try",
        "/opt",
        "/opt",
        bind,
        str(root),
        str(root),
        "--chdir",
        str(root),
    ]
    if not allow_network:
        parts.append("--unshare-net")
    parts.extend(["--", "/bin/sh", "-lc", command])
    return " ".join(shlex.quote(p) for p in parts), None


def _parse_extra_binds() -> list[str]:
    """Optional comma-separated host:container[:ro|rw] binds; denylist enforced."""
    raw = (os.environ.get("KAGEHA_SANDBOX_DOCKER_BINDS") or "").strip()
    if not raw:
        return []
    out: list[str] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        host = item.split(":", 1)[0]
        try:
            host_path = Path(host).resolve()
        except Exception:  # noqa: BLE001
            continue
        hs = str(host_path)
        if any(hs == p or hs.startswith(p + os.sep) for p in _BLOCKED_BIND_PREFIXES):
            continue
        if hs.endswith("docker.sock") or host_path.name == "docker.sock":
            continue
        # Normalize mode
        if item.count(":") == 1:
            item = f"{item}:ro"
        out.append(item)
    return out


def _wrap_docker(
    command: str, cwd: Path, *, allow_network: bool
) -> tuple[str, Path | None]:
    exe = shutil.which("docker")
    if not exe:
        return command, None
    root = cwd.resolve()
    access = workspace_access()
    mount_mode = "rw" if access == "rw" else "ro"
    net = docker_network_mode(allow_network=allow_network)
    image = os.environ.get("KAGEHA_SANDBOX_DOCKER_IMAGE", "python:3.12-slim")
    inner = _shell_quote(command)
    hardened = (
        f"{shlex.quote(exe)} run --rm --network={shlex.quote(net)} "
        f"--pids-limit=256 --cap-drop=ALL --security-opt=no-new-privileges "
    )
    if docker_read_only_root():
        hardened += (
            "--read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m "
            "--tmpfs /var/tmp:rw,nosuid,nodev,size=64m "
        )
    # Match host uid/gid on Linux so the workspace bind stays writable.
    # Root inside container: KAGEHA_SANDBOX_DOCKER_ROOT=1.
    if platform.system() == "Linux" and not _env_truthy("KAGEHA_SANDBOX_DOCKER_ROOT"):
        try:
            hardened += f"--user={os.getuid()}:{os.getgid()} "
        except AttributeError:
            pass
    for bind in _parse_extra_binds():
        hardened += f"-v {shlex.quote(bind)} "
    hardened += (
        f"-v {shlex.quote(str(root))}:/work:{mount_mode} -w /work "
        f"{shlex.quote(image)} /bin/sh -lc '{inner}'"
    )
    return hardened, None


def _ssh_sync_mode() -> str:
    """``bidirectional`` (default), ``push``, or ``none``."""
    raw = (os.environ.get("KAGEHA_SANDBOX_SSH_SYNC") or "bidirectional").strip().lower()
    if raw in {"0", "false", "off", "none"}:
        return "none"
    if raw in {"push", "push-only"}:
        return "push"
    return "bidirectional"


def _wrap_ssh(
    command: str, cwd: Path, *, allow_network: bool
) -> tuple[str, Path | None]:
    """Run command on a remote host via BatchMode ssh (explicit opt-in).

    Workspace is synced with a tar pipe into ``KAGEHA_SANDBOX_SSH_WORKDIR``
    (default ``/tmp/kageha-work``), then the command runs there.

    By default (``KAGEHA_SANDBOX_SSH_SYNC=bidirectional``) remote artifacts are
    tar-piped back into the local cwd after the command. Set ``push`` to disable
    pull-back, or ``none`` to skip sync entirely (command only).
    ``allow_network`` is accepted for API parity (remote host networking).
    """
    del allow_network  # remote host controls egress
    exe = shutil.which("ssh")
    host = (os.environ.get("KAGEHA_SANDBOX_SSH_HOST") or "").strip()
    if not exe or not host:
        return command, None
    user = (os.environ.get("KAGEHA_SANDBOX_SSH_USER") or "").strip()
    port = (os.environ.get("KAGEHA_SANDBOX_SSH_PORT") or "22").strip() or "22"
    identity = (os.environ.get("KAGEHA_SANDBOX_SSH_IDENTITY") or "").strip()
    workdir = (
        os.environ.get("KAGEHA_SANDBOX_SSH_WORKDIR") or "/tmp/kageha-work"
    ).strip()
    target = f"{user}@{host}" if user else host
    ssh_opts = [
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-p",
        port,
    ]
    if identity:
        ssh_opts.extend(["-i", identity])
    root = cwd.resolve()
    inner = _shell_quote(command)
    sync = _ssh_sync_mode()
    ssh_base = (
        f"{shlex.quote(exe)} {' '.join(shlex.quote(o) for o in ssh_opts)} "
        f"{shlex.quote(target)}"
    )

    if sync == "none":
        remote = f"cd {shlex.quote(workdir)} && /bin/bash -lc '{inner}'"
        return f"{ssh_base} {shlex.quote(remote)}", None

    # Push local → remote
    push_remote = (
        f"mkdir -p {shlex.quote(workdir)} && "
        f"tar -x -C {shlex.quote(workdir)} && "
        f"cd {shlex.quote(workdir)} && /bin/bash -lc '{inner}'"
    )
    push = (
        f"tar -c -C {shlex.quote(str(root))} . | "
        f"{ssh_base} {shlex.quote(push_remote)}"
    )
    if sync == "push":
        return push, None

    # Bidirectional: after remote command, tar remote workdir back into local cwd.
    # Exclude common junk; overwrite local with remote changes (artifacts).
    pull_remote = f"tar -c -C {shlex.quote(workdir)} ."
    pull = (
        f"{ssh_base} {shlex.quote(pull_remote)} | "
        f"tar -x -C {shlex.quote(str(root))}"
    )
    wrapped = f"({push}) && ({pull})"
    return wrapped, None


def describe_sandbox_for_help() -> str:
    st = sandbox_status()
    return (
        f"profile={st.profile} requested={st.requested} "
        f"available={st.available} workspace={workspace_access()} "
        f"({st.detail})"
    )
