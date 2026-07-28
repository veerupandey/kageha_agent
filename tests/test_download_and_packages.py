"""download_file + install_python_packages + bash steering."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.builtin import register as register_builtin


def _ctx(tmp_path: Path) -> HarnessContext:
    root = tmp_path / "session"
    root.mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(exist_ok=True)
    project = tmp_path / "project"
    project.mkdir()
    ws = SessionWorkspace(run_id="test", root=root)
    return HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
        project_root=str(project),
    )


@pytest.mark.asyncio
async def test_download_file_saves_bytes(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    reg = register_builtin(ctx)
    assert "download_file" in reg.tools

    class _Resp:
        status_code = 200

        async def aiter_bytes(self):
            yield b"\x89PNG"
            yield b"data"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    client = MagicMock()
    client.stream = MagicMock(return_value=_Resp())
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=client):
        raw = await reg.get("download_file").call(
            url="https://cdn.example/product.png",
            path="artifacts/product.png",
        )
    data = json.loads(raw)
    assert data["path"].endswith("artifacts/product.png")
    saved = ctx.session_root() / "artifacts" / "product.png"
    assert saved.read_bytes() == b"\x89PNGdata"


@pytest.mark.asyncio
async def test_bash_steers_curl_image_to_download_file(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    reg = register_builtin(ctx)
    out = await reg.get("bash").call(
        command="curl -o artifacts/x.png https://example.com/x.png"
    )
    assert "download_file" in out
    assert "ERROR" in out


@pytest.mark.asyncio
async def test_bash_steers_pip_google_genai(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    reg = register_builtin(ctx)
    out = await reg.get("bash").call(command="pip install google-genai")
    assert "nano_banana" in out
    assert "ERROR" in out


@pytest.mark.asyncio
async def test_install_python_packages_blocks_genai_sdk(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    reg = register_builtin(ctx)
    out = await reg.get("install_python_packages").call(packages="google-genai pillow")
    assert "ERROR" in out
    assert "nano_banana" in out


@pytest.mark.asyncio
async def test_install_python_packages_runs_pip_target(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    reg = register_builtin(ctx)
    fake = SimpleNamespace(
        exit_code=0,
        stdout="Successfully installed pillow",
        stderr="",
        sandboxed=True,
        security_profile="approval_fallback",
    )
    with patch(
        "kageha.harness.tools.builtin.run_shell",
        new=AsyncMock(return_value=fake),
    ) as run:
        raw = await reg.get("install_python_packages").call(packages="pillow")
    data = json.loads(raw)
    assert data["ok"] is True
    assert "pillow" in data["packages"]
    assert ".kageha_pkgs" in data["target"]
    cmd = run.await_args.args[0]
    assert "--target" in cmd
    assert "pillow" in cmd


@pytest.mark.asyncio
async def test_bash_network_true_requests_sandbox_network(
    tmp_path: Path, monkeypatch
) -> None:
    from kageha.harness import approvals as ap

    monkeypatch.delenv("KAGEHA_SANDBOX_ALLOW_NETWORK", raising=False)
    ap._PROCESS_PERMISSIONS.update(
        {"auto_approve": False, "sandbox_network": False, "scope": "ask"}
    )
    monkeypatch.setattr(ap, "_load_allowlist", lambda: set())
    asked: list[str] = []

    async def approver(req):  # noqa: ANN001
        asked.append(req.risk_class)
        assert "SANDBOX + NETWORK" in (req.detail or "")
        return True

    root = tmp_path / "session"
    root.mkdir()
    (root / "artifacts").mkdir()
    gate = ApprovalGate(auto_approve=False, approver=approver)
    gate._allowlist = set()
    ctx = HarnessContext(
        workspace=SessionWorkspace(run_id="t", root=root),
        approvals=gate,
        router=SimpleNamespace(),
        project_root=str(tmp_path / "proj"),
    )
    (tmp_path / "proj").mkdir()
    reg = register_builtin(ctx)
    fake = SimpleNamespace(
        exit_code=0,
        stdout="ok",
        stderr="",
        sandboxed=True,
        security_profile="approval_fallback",
    )
    with patch(
        "kageha.harness.tools.builtin.run_shell",
        new=AsyncMock(return_value=fake),
    ) as run:
        raw = await reg.get("bash").call(command="echo hi", network=True)
    data = json.loads(raw)
    assert data["allow_network"] is True
    assert data["elevated"] is False
    assert asked == ["shell_network_or_destructive"]
    assert run.await_args.kwargs["allow_network"] is True
    assert run.await_args.kwargs["elevated"] is False


@pytest.mark.asyncio
async def test_bash_elevated_always_asks_even_with_auto_approve(tmp_path: Path) -> None:
    asked: list[str] = []

    async def approver(req):  # noqa: ANN001
        asked.append(req.risk_class)
        assert "HOST ESCAPE" in (req.detail or "")
        return True

    root = tmp_path / "session"
    root.mkdir()
    gate = ApprovalGate(auto_approve=True, approver=approver)
    ctx = HarnessContext(
        workspace=SessionWorkspace(run_id="t", root=root),
        approvals=gate,
        router=SimpleNamespace(),
    )
    reg = register_builtin(ctx)
    fake = SimpleNamespace(
        exit_code=0,
        stdout="ok",
        stderr="",
        sandboxed=False,
        security_profile="approval_fallback",
    )
    with patch(
        "kageha.harness.tools.builtin.run_shell",
        new=AsyncMock(return_value=fake),
    ) as run:
        raw = await reg.get("bash").call(command="echo hi", elevated=True)
    data = json.loads(raw)
    assert data["elevated"] is True
    assert asked == ["shell_elevated"]  # require_explicit, not auto-skipped
    assert run.await_args.kwargs["elevated"] is True


def test_seatbelt_allows_extra_write_roots(tmp_path: Path, monkeypatch) -> None:
    from kageha.harness.shell_sandbox import wrap_shell_command

    monkeypatch.setenv("KAGEHA_SANDBOX", "seatbelt")
    import shutil

    if not shutil.which("sandbox-exec"):
        pytest.skip("no sandbox-exec")
    # Session roots are outside the coding cwd in production (~/.kageha/sessions).
    coding = tmp_path / "project"
    session = tmp_path / "session"
    coding.mkdir()
    session.mkdir()
    cmd, cleanup = wrap_shell_command(
        "echo hi",
        coding,
        allow_network=True,
        extra_write_roots=[session],
    )
    assert cleanup is not None
    profile = cleanup.read_text()
    assert str(session.resolve()) in profile
    assert "(allow network*)" in profile
    cleanup.unlink(missing_ok=True)
