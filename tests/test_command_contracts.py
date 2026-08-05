"""Every advertised command must map to a real handler surface."""

from kageha.chat.browser_commands import BROWSER_ACTIONS
from kageha.chat.computer_commands import _ADMIN_ACTIONS
from kageha.chat.line_edit import _SLASH_COMMANDS, _SUBCOMMANDS
from kageha.webui.server import (
    _WEBUI_SLASH_BASE,
    _WEBUI_SLASH_BROWSER,
    _WEBUI_SLASH_COMPUTER,
)


def _all_webui_commands():
    return [*_WEBUI_SLASH_BASE, *_WEBUI_SLASH_BROWSER, *_WEBUI_SLASH_COMPUTER]


def test_webui_catalog_has_unique_ids_and_labels():
    commands = _all_webui_commands()
    ids = [command["id"] for command in commands]
    labels = [command["label"] for command in commands]
    assert len(ids) == len(set(ids))
    assert len(labels) == len(set(labels))
    assert all(label.startswith("/") for label in labels)


def test_advertised_browser_commands_are_handled():
    for command in _WEBUI_SLASH_BROWSER:
        parts = command["label"].split()
        if parts[0] == "/research" or len(parts) == 1:
            continue
        assert parts[1] in BROWSER_ACTIONS, command


def test_advertised_computer_admin_commands_are_handled():
    for command in _WEBUI_SLASH_COMPUTER:
        parts = command["label"].split()
        if len(parts) == 1:
            continue
        assert parts[1] in _ADMIN_ACTIONS, command


def test_cli_browser_completion_matches_supported_actions():
    assert "diagnose" in _SUBCOMMANDS["/browser"]
    for command in _SLASH_COMMANDS:
        parts = command.split()
        if parts[0] == "/browser" and len(parts) > 1:
            assert parts[1] in BROWSER_ACTIONS, command
