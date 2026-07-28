"""Session workspace + sandboxed shell execution."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kageha.io import atomic_write_json, atomic_write_text

_SESSION_META = "session.json"


def _sessions_root() -> Path:
    from kageha.config import sessions_dir

    return sessions_dir()


@dataclass
class SessionWorkspace:
    run_id: str
    root: Path
    meta: dict = field(default_factory=dict)

    @classmethod
    def create(cls, run_id: str | None = None) -> SessionWorkspace:
        rid = run_id or uuid.uuid4().hex[:12]
        root = _sessions_root() / rid
        root.mkdir(parents=True, exist_ok=True)
        (root / "artifacts").mkdir(exist_ok=True)
        (root / "_memory").mkdir(exist_ok=True)
        return cls(run_id=rid, root=root)

    @classmethod
    def open(cls, run_id: str) -> SessionWorkspace:
        root = _sessions_root() / run_id
        if not root.is_dir():
            raise FileNotFoundError(f"Session not found: {run_id}")
        ws = cls(run_id=run_id, root=root)
        ws.meta = ws.load_session_meta()
        return ws

    def path(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError("Path escapes session workspace")
        return p

    def load_session_meta(self) -> dict[str, Any]:
        path = self.root / _SESSION_META
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text())
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def save_session_meta(self, meta: dict[str, Any] | None = None) -> None:
        data = dict(meta if meta is not None else self.meta)
        self.meta = data
        path = self.root / _SESSION_META
        atomic_write_json(path, data)

    def get_model_override(self) -> str | None:
        meta = self.load_session_meta()
        mid = meta.get("model_override")
        return str(mid) if mid else None

    def set_model_override(self, model_id: str | None) -> None:
        meta = self.load_session_meta()
        if model_id:
            meta["model_override"] = model_id
        else:
            meta.pop("model_override", None)
        self.save_session_meta(meta)

    def get_model_once(self) -> str | None:
        meta = self.load_session_meta()
        mid = meta.get("model_once")
        return str(mid) if mid else None

    def set_model_once(self, model_id: str | None) -> None:
        meta = self.load_session_meta()
        if model_id:
            meta["model_once"] = model_id
        else:
            meta.pop("model_once", None)
        self.save_session_meta(meta)

    def get_model_role_overrides(self) -> dict[str, str]:
        """Logical slots: planner / executor → model ids."""
        meta = self.load_session_meta()
        raw = meta.get("model_role_overrides") or {}
        if not isinstance(raw, dict):
            return {}
        out: dict[str, str] = {}
        for k, v in raw.items():
            slot = str(k).strip().lower()
            mid = str(v).strip() if v is not None else ""
            if slot in {"planner", "executor"} and mid:
                out[slot] = mid
        return out

    def set_model_role_overrides(self, overrides: dict[str, str] | None) -> None:
        meta = self.load_session_meta()
        clean: dict[str, str] = {}
        for k, v in (overrides or {}).items():
            slot = str(k).strip().lower()
            mid = str(v).strip() if v is not None else ""
            if slot in {"planner", "executor"} and mid:
                clean[slot] = mid
        if clean:
            meta["model_role_overrides"] = clean
        else:
            meta.pop("model_role_overrides", None)
        self.save_session_meta(meta)

    def set_model_role_override(self, slot: str, model_id: str | None) -> dict[str, str]:
        cur = self.get_model_role_overrides()
        key = str(slot).strip().lower()
        if key not in {"planner", "executor"}:
            return cur
        if model_id:
            cur[key] = str(model_id).strip()
        else:
            cur.pop(key, None)
        self.set_model_role_overrides(cur)
        return cur

    def write_text(self, rel: str, content: str) -> Path:
        p = self.path(rel)
        return atomic_write_text(p, content)

    def read_text(self, rel: str) -> str:
        return self.path(rel).read_text()

    def list_files(self, rel: str = ".") -> list[str]:
        base = self.path(rel)
        if not base.exists():
            return []
        out: list[str] = []
        for p in sorted(base.rglob("*")):
            if p.is_file():
                out.append(str(p.relative_to(self.root)))
        return out

    def export_to(self, destination: Path) -> list[str]:
        """Copy generated files to an explicit user-visible destination.

        Session-control files stay private. Relative artifact paths are preserved,
        so exporting to a project root materializes ``outputs/...`` exactly where
        the task requested it.
        """
        dest = destination.expanduser().resolve()
        if dest == self.root.resolve() or str(dest).startswith(str(self.root.resolve()) + os.sep):
            raise ValueError("Export destination must be outside the session workspace")
        dest.mkdir(parents=True, exist_ok=True)
        internal_roots = {"_memory", "checkpoints"}
        internal_files = {
            "events.jsonl",
            "goal_card.json",
            "plan.json",
            "result.md",
            "todo.md",
            "session.json",
            "task_state.json",
            "chat.jsonl",
        }
        copied: list[str] = []
        for rel in self.list_files():
            path = Path(rel)
            if rel in internal_files or (path.parts and path.parts[0] in internal_roots):
                continue
            source = self.path(rel)
            target = (dest / path).resolve()
            if not str(target).startswith(str(dest) + os.sep):
                raise ValueError(f"Export path escapes destination: {rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(rel)
        return copied


@dataclass
class ShellResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    sandboxed: bool = False
    security_profile: str = "approval_fallback"

    def truncate(self, limit: int = 8000) -> "ShellResult":
        def clip(s: str) -> str:
            if len(s) <= limit:
                return s
            return s[:limit] + f"\n...[truncated {len(s) - limit} chars]"

        return ShellResult(
            command=self.command,
            exit_code=self.exit_code,
            stdout=clip(self.stdout),
            stderr=clip(self.stderr),
            sandboxed=self.sandboxed,
            security_profile=self.security_profile,
        )


async def run_shell(
    command: str,
    cwd: Path,
    *,
    timeout: float = 120.0,
    env: dict[str, str] | None = None,
    allow_network: bool = False,
    elevated: bool = False,
    security_profile: str | None = None,
) -> ShellResult:
    """Run a shell command with cwd restricted to session root (+ OS sandbox).

    ``elevated=True`` skips seatbelt/bwrap/docker (OpenClaw-style host escape).
    Modal (``KAGEHA_SANDBOX=modal``) uses ``TerminalBackend`` instead of wrap.
    """
    from kageha.config import security_profile as configured_security_profile
    from kageha.harness.shell_sandbox import sandbox_status, wrap_shell_command
    from kageha.harness.terminal_backend import resolve_terminal_backend

    selected_security = configured_security_profile(security_profile)
    isolation = sandbox_status()
    sandboxed = (
        not elevated
        and isolation.profile != "off"
        and isolation.available
    )
    if selected_security == "strict" and not sandboxed:
        return ShellResult(
            command=command,
            exit_code=126,
            stdout="",
            stderr=(
                "DENIED: strict security profile requires OS isolation; "
                f"{isolation.profile}: {isolation.detail}"
            ),
            sandboxed=False,
            security_profile=selected_security,
        )

    merged = os.environ.copy()
    if env:
        merged.update(env)
    # Opt-in network for seatbelt/docker when caller already passed HITL.
    if os.environ.get("KAGEHA_SANDBOX_ALLOW_NETWORK", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        allow_network = True

    # Keep temp writes inside the coding root (seatbelt no longer allows host /tmp).
    if not elevated and sandboxed:
        from kageha.harness.shell_sandbox import scratch_dir_for

        scratch = str(scratch_dir_for(cwd))
        merged.setdefault("TMPDIR", scratch)
        merged.setdefault("TMP", scratch)
        merged.setdefault("TEMP", scratch)

    # Cloud/serverless backends (Modal) — skip local wrap entirely.
    if not elevated:
        backend = resolve_terminal_backend(isolation.profile)
        if backend is not None:
            result = await backend.exec(
                command,
                cwd,
                timeout=timeout,
                env=merged,
                allow_network=allow_network,
            )
            return ShellResult(
                command=command,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                sandboxed=result.exit_code != 78 and isolation.available,
                security_profile=selected_security,
            ).truncate()

    extra_roots: list[Path] = []
    session_env = (merged.get("KAGEHA_SESSION") or "").strip()
    if session_env:
        try:
            extra_roots.append(Path(session_env).expanduser().resolve())
        except Exception:  # noqa: BLE001
            pass
    artifacts_env = (merged.get("KAGEHA_ARTIFACTS") or "").strip()
    if artifacts_env:
        try:
            extra_roots.append(Path(artifacts_env).expanduser().resolve())
        except Exception:  # noqa: BLE001
            pass

    wrapped, cleanup = wrap_shell_command(
        command,
        cwd,
        allow_network=allow_network,
        elevated=elevated,
        extra_write_roots=extra_roots,
    )
    # DEVNULL stdin: interactive bash `read` / hung pipes must not steal Terminal
    # keystrokes meant for HITL (/dev/tty race in approvals.py).
    try:
        proc = await asyncio.create_subprocess_shell(
            wrapped,
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return ShellResult(
                command=command,
                exit_code=124,
                stdout="",
                stderr="timeout",
                sandboxed=sandboxed,
                security_profile=selected_security,
            )
        return ShellResult(
            command=command,
            exit_code=proc.returncode or 0,
            stdout=stdout_b.decode(errors="replace"),
            stderr=stderr_b.decode(errors="replace"),
            sandboxed=sandboxed,
            security_profile=selected_security,
        ).truncate()
    finally:
        if cleanup is not None:
            try:
                cleanup.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass


def quote(s: str) -> str:
    return shlex.quote(s)
