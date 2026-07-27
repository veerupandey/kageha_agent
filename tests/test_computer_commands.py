"""Native /computer slash commands."""

from __future__ import annotations

from pathlib import Path

import pytest

from kageha.chat import computer_commands
from kageha.harness.tools import computer_prefs as prefs_mod
from kageha.harness.tool_packs import resolve_enabled_packs


@pytest.fixture()
def isolated_computer_prefs(tmp_path: Path, monkeypatch):
    path = tmp_path / "computer.json"
    allow = tmp_path / "computer_apps.json"
    monkeypatch.setattr(prefs_mod, "prefs_path", lambda: path)
    monkeypatch.setattr(
        "kageha.harness.tools.computer_allowlist.allowlist_path",
        lambda: allow,
    )
    monkeypatch.delenv("KAGEHA_COMPUTER", raising=False)
    monkeypatch.delenv("KAGEHA_COMPUTER_FROM_PREFS", raising=False)
    monkeypatch.delenv("KAGEHA_TOOL_PACKS", raising=False)
    prefs_mod.save_computer_prefs(prefs_mod.ComputerPrefs(pack="auto"))
    prefs_mod.apply_computer_prefs()
    return path


@pytest.mark.asyncio
async def test_computer_status_and_help(isolated_computer_prefs) -> None:
    handled, msg = await computer_commands.handle_computer_command("/computer")
    assert handled
    assert "Computer-use skill" in msg
    assert "/computer status" in msg
    assert "pack mode" not in msg

    handled, msg = await computer_commands.handle_computer_command("/computer status")
    assert handled
    assert "Computer-use" in msg
    assert "pack mode" in msg

    handled, msg = await computer_commands.handle_computer_command("/computer help")
    assert handled
    assert "/computer doctor" in msg


@pytest.mark.asyncio
async def test_computer_pack_off_on_auto(isolated_computer_prefs, monkeypatch) -> None:
    monkeypatch.setattr(
        "kageha.harness.tools.computer_driver.driver_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "kageha.harness.tool_packs.platform.system",
        lambda: "Darwin",
    )
    # Explicit yaml/env pack must still honor /computer pack off.
    monkeypatch.setenv("KAGEHA_TOOL_PACKS", "computer")

    handled, msg = await computer_commands.handle_computer_command("/computer pack off")
    assert handled
    assert "pack mode: off" in msg
    assert "computer" not in resolve_enabled_packs()

    handled, msg = await computer_commands.handle_computer_command("/computer pack on")
    assert handled
    assert "pack mode: on" in msg
    assert "computer" in resolve_enabled_packs()

    handled, msg = await computer_commands.handle_computer_command("/computer pack auto")
    assert handled
    assert "pack mode: auto" in msg
    assert "computer" in resolve_enabled_packs()


@pytest.mark.asyncio
async def test_computer_allow_and_allowlist(isolated_computer_prefs) -> None:
    handled, msg = await computer_commands.handle_computer_command(
        "/computer allow com.apple.calculator always"
    )
    assert handled
    assert "com.apple.calculator" in msg
    assert "always" in msg

    handled, msg = await computer_commands.handle_computer_command("/computer allowlist")
    assert handled
    assert "com.apple.calculator" in msg

    handled, msg = await computer_commands.handle_computer_command(
        "/computer clear com.apple.calculator"
    )
    assert handled
    assert "Cleared" in msg


@pytest.mark.asyncio
async def test_computer_task_falls_through_to_skill(isolated_computer_prefs) -> None:
    """``/computer <task>`` is not an admin command — agent + computer_use skill."""
    handled, msg = await computer_commands.handle_computer_command(
        "/computer open Calculator and compute 8+9"
    )
    assert handled is False
    assert msg == ""
    assert computer_commands.is_computer_admin_command("/computer") is True
    assert computer_commands.is_computer_admin_command("/computer status") is True
    assert (
        computer_commands.is_computer_admin_command(
            "/computer open Calculator"
        )
        is False
    )


def test_computer_slash_activates_computer_use_skill() -> None:
    from kageha.memory.skills import (
        SkillRegistry,
        parse_skill_invocations,
        strip_skill_invocations,
    )

    reg = SkillRegistry()
    assert "computer_use" in reg.skills
    msg = "/computer open Calculator and compute 8+9"
    forced = parse_skill_invocations(msg, reg)
    assert forced == ["computer_use"]
    assert strip_skill_invocations(msg, forced) == "open Calculator and compute 8+9"
    loaded = reg.auto_load_for_task(msg, force_names=forced, limit=2)
    assert loaded.names == ["computer_use"]
    # Admin forms must not force the skill
    assert parse_skill_invocations("/computer", reg) == []
    assert parse_skill_invocations("/computer status", reg) == []
    assert parse_skill_invocations("/computer doctor", reg) == []
