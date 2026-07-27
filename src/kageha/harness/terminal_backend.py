"""Pluggable terminal backends for sandboxed shell execution.

Local wrap profiles (seatbelt/bwrap/docker/ssh) stay in ``shell_sandbox``.
Cloud/serverless backends (Modal) implement ``TerminalBackend.exec``.
"""

from __future__ import annotations

import asyncio
import io
import os
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class TerminalExecResult:
    exit_code: int
    stdout: str
    stderr: str
    backend: str


@runtime_checkable
class TerminalBackend(Protocol):
    name: str

    def available(self) -> tuple[bool, str]:
        """Return (ok, detail)."""
        ...

    async def exec(
        self,
        command: str,
        cwd: Path,
        *,
        timeout: float = 120.0,
        env: dict[str, str] | None = None,
        allow_network: bool = False,
    ) -> TerminalExecResult:
        ...


def modal_backend() -> "ModalTerminalBackend":
    return ModalTerminalBackend()


class ModalTerminalBackend:
    """Ephemeral Modal Sandbox with tar-synced workspace (SSH-shaped)."""

    name = "modal"

    def available(self) -> tuple[bool, str]:
        try:
            import modal  # noqa: F401
        except ImportError:
            return False, "modal package missing (pip install 'kageha[sandbox]' or modal)"
        token_id = (os.environ.get("MODAL_TOKEN_ID") or "").strip()
        token_secret = (os.environ.get("MODAL_TOKEN_SECRET") or "").strip()
        # Modal also accepts `modal token` stored credentials.
        if token_id and token_secret:
            return True, "MODAL_TOKEN_ID/SECRET set"
        if (os.environ.get("MODAL_TOKEN") or "").strip():
            return True, "MODAL_TOKEN set"
        # CLI config may exist even without env vars.
        cfg = Path.home() / ".modal.toml"
        if cfg.is_file():
            return True, f"credentials via {cfg}"
        return False, "Modal auth missing (modal token new / MODAL_TOKEN_*)"

    async def exec(
        self,
        command: str,
        cwd: Path,
        *,
        timeout: float = 120.0,
        env: dict[str, str] | None = None,
        allow_network: bool = False,
    ) -> TerminalExecResult:
        del allow_network  # Modal sandboxes have network by default
        ok, detail = self.available()
        if not ok:
            return TerminalExecResult(
                exit_code=78,
                stdout="",
                stderr=f"ERROR: modal backend unavailable: {detail}",
                backend=self.name,
            )
        return await asyncio.to_thread(
            self._exec_sync,
            command,
            cwd.resolve(),
            timeout,
            dict(env or {}),
        )

    def _exec_sync(
        self,
        command: str,
        cwd: Path,
        timeout: float,
        env: dict[str, str],
    ) -> TerminalExecResult:
        import modal

        image_ref = (
            os.environ.get("KAGEHA_SANDBOX_MODAL_IMAGE") or "debian:bookworm-slim"
        ).strip()
        app_name = (
            os.environ.get("KAGEHA_SANDBOX_MODAL_APP") or "kageha-sandbox"
        ).strip()
        workdir = "/work"
        sync = (
            os.environ.get("KAGEHA_SANDBOX_MODAL_SYNC") or "bidirectional"
        ).strip().lower()
        if sync in {"0", "false", "off", "none"}:
            sync = "none"
        elif sync in {"push", "push-only"}:
            sync = "push"
        else:
            sync = "bidirectional"

        try:
            app = modal.App.lookup(app_name, create_if_missing=True)
            image = modal.Image.from_registry(image_ref)
            sb = modal.Sandbox.create(
                app=app,
                image=image,
                timeout=max(30, int(timeout) + 30),
                workdir=workdir,
            )
        except Exception as exc:  # noqa: BLE001
            return TerminalExecResult(
                exit_code=78,
                stdout="",
                stderr=f"ERROR: modal sandbox create failed: {exc}",
                backend=self.name,
            )

        try:
            if sync != "none" and cwd.is_dir():
                self._push_workspace(sb, cwd, workdir)

            # Export a small env subset into the remote shell.
            env_exports = " ".join(
                f"{k}={_shell_single_quote(v)}"
                for k, v in env.items()
                if k.startswith("KAGEHA_") or k in {"PATH", "HOME", "LANG"}
            )
            remote = (
                f"mkdir -p {workdir} && cd {workdir} && "
                + (f"export {env_exports} && " if env_exports else "")
                + f"/bin/bash -lc {_shell_single_quote(command)}"
            )
            proc = sb.exec("bash", "-lc", remote)
            try:
                stdout = proc.stdout.read()
                stderr = proc.stderr.read()
                code = int(proc.wait())
            except Exception as exc:  # noqa: BLE001
                return TerminalExecResult(
                    exit_code=124,
                    stdout="",
                    stderr=f"ERROR: modal exec failed: {exc}",
                    backend=self.name,
                )

            if sync == "bidirectional" and cwd.is_dir():
                try:
                    self._pull_workspace(sb, cwd, workdir)
                except Exception as exc:  # noqa: BLE001
                    stderr = (stderr or "") + f"\n[modal sync pull warning: {exc}]"

            return TerminalExecResult(
                exit_code=code,
                stdout=stdout if isinstance(stdout, str) else stdout.decode(errors="replace"),
                stderr=stderr if isinstance(stderr, str) else stderr.decode(errors="replace"),
                backend=self.name,
            )
        finally:
            try:
                sb.terminate()
            except Exception:  # noqa: BLE001
                pass

    def _push_workspace(self, sb: object, cwd: Path, workdir: str) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            tar.add(str(cwd), arcname=".")
        buf.seek(0)
        # Write tar remotely then extract.
        remote_tar = "/tmp/kageha-push.tar"
        with sb.open(remote_tar, "wb") as fh:  # type: ignore[attr-defined]
            fh.write(buf.read())
        p = sb.exec(  # type: ignore[attr-defined]
            "bash",
            "-lc",
            f"mkdir -p {workdir} && tar -xf {remote_tar} -C {workdir} && rm -f {remote_tar}",
        )
        p.wait()

    def _pull_workspace(self, sb: object, cwd: Path, workdir: str) -> None:
        remote_tar = "/tmp/kageha-pull.tar"
        p = sb.exec(  # type: ignore[attr-defined]
            "bash",
            "-lc",
            f"tar -cf {remote_tar} -C {workdir} .",
        )
        p.wait()
        with sb.open(remote_tar, "rb") as fh:  # type: ignore[attr-defined]
            data = fh.read()
        with tempfile.TemporaryDirectory(prefix="kageha-modal-") as tmp:
            tar_path = Path(tmp) / "pull.tar"
            tar_path.write_bytes(data)
            with tarfile.open(tar_path, "r") as tar:
                tar.extractall(path=cwd)


def _shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def resolve_terminal_backend(profile: str | None = None) -> TerminalBackend | None:
    """Return a cloud TerminalBackend when profile needs one; else None (local wrap)."""
    from kageha.config import sandbox_profile

    mode = (profile or sandbox_profile()).strip().lower()
    if mode == "modal":
        return modal_backend()
    return None


def describe_modal_status() -> str:
    ok, detail = ModalTerminalBackend().available()
    which = shutil.which("modal") or "missing"
    return f"modal_cli={which} available={ok} ({detail})"
