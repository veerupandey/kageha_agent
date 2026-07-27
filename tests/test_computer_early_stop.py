"""Research-backed computer early-stop + history compression."""

from __future__ import annotations

import json

from kageha.context.assembler import ContextAssembler
from kageha.harness.tools.computer_early_stop import (
    compress_computer_tool_content,
    computer_compound_success,
    has_verified_computer_evidence,
    select_computer_early_stop,
)
from kageha.models.base import ChatMessage


def test_compound_sequence_with_readings_is_early_stop():
    payload = {
        "ok": True,
        "mode": "adaptive_text_from_labels",
        "text": "8+9=",
        "readings": [
            {"ref": "display:0", "value": "8+9"},
            {"ref": "display:1", "value": "17"},
        ],
        "result": {"ok": True, "characters": 4, "path": "key_events"},
        "loop": "Quote readings and stop.",
    }
    hit = computer_compound_success(
        json.dumps(payload), tool="computer_click_sequence"
    )
    assert hit is not None
    assert hit.mode == "adaptive_text_from_labels"
    assert hit.answer == "Readings: 17"
    assert "17" in hit.evidence


def test_type_text_without_expected_result_not_early_stop():
    """Dirty Calculator display must not false-succeed (fail-closed)."""
    payload = {
        "ok": True,
        "mode": "type_text",
        "text": "8+9=",
        "readings": [
            {"value": "8+98+9"},
            {"value": "115"},
        ],
    }
    assert (
        computer_compound_success(
            json.dumps(payload), tool="computer_click_sequence"
        )
        is None
    )


def test_single_click_is_not_grouped_early_stop():
    """Only compound/grouped tools qualify (OSWorld-Human grouped-action unit)."""
    payload = {
        "ok": True,
        "mode": "ax_ref",
        "readings": [{"value": "1"}],
    }
    assert (
        computer_compound_success(json.dumps(payload), tool="computer_click") is None
    )


def test_failed_or_empty_readings_not_early_stop():
    assert (
        computer_compound_success(
            json.dumps({"ok": False, "mode": "type_text", "readings": [{"value": "x"}]}),
            tool="computer_click_sequence",
        )
        is None
    )
    assert (
        computer_compound_success(
            json.dumps({"ok": True, "mode": "type_text", "readings": []}),
            tool="computer_click_sequence",
        )
        is None
    )


def test_select_last_successful_compound():
    rows = [
        ("computer_list_apps", '{"apps":[]}'),
        (
            "computer_click_sequence",
            json.dumps(
                {
                    "ok": True,
                    "mode": "type_text",
                    "readings": [{"value": "17"}],
                }
            ),
        ),
    ]
    hit = select_computer_early_stop(rows)
    assert hit is not None
    assert hit.answer == "Readings: 17"


def test_verified_computer_evidence_rejects_unverifiable_and_files():
    """Codex session bug: unverifiable type + write_file must not count as success."""
    unverifiable = json.dumps(
        {
            "ok": True,
            "chars": 11,
            "result": {"effect": "unverifiable", "path": "key_events"},
        }
    )
    assert not has_verified_computer_evidence(
        [
            ("computer_get_state", '{"ok":true,"app":"ChatGPT"}'),
            ("computer_type", unverifiable),
            ("write_file", "Wrote 272 bytes to codex_interaction.txt"),
            ("todo_write", "updated"),
        ]
    )
    assert has_verified_computer_evidence(
        [
            (
                "computer_type",
                json.dumps(
                    {
                        "ok": True,
                        "chars": 3,
                        "verified": True,
                        "result": {"path": "key_events"},
                    }
                ),
            )
        ]
    )
    assert has_verified_computer_evidence(
        [
            (
                "computer_click_sequence",
                json.dumps(
                    {
                        "ok": True,
                        "mode": "type_text",
                        "readings": [{"value": "17"}],
                    }
                ),
            )
        ]
    )


def test_compress_computer_tool_drops_heavy_fields():
    raw = json.dumps(
        {
            "ok": True,
            "mode": "type_text",
            "readings": [{"value": "17"}],
            "snapshot": "e0 button " * 200,
            "tree_markdown": "huge " * 200,
            "result": {"characters": 4, "path": "key_events", "escalation": {"x": 1}},
            "loop": "stop",
        }
    )
    out = compress_computer_tool_content(raw, tool_name="computer_click_sequence")
    data = json.loads(out)
    assert data["ok"] is True
    assert data["readings"][0]["value"] == "17"
    assert "snapshot" not in data
    assert "tree_markdown" not in data
    assert "result" not in data


def test_assembler_applies_computer_compression():
    assembler = ContextAssembler()
    heavy = json.dumps(
        {
            "ok": True,
            "mode": "type_text",
            "readings": [{"value": "42"}],
            "snapshot": "x" * 5000,
            "tree_markdown": "y" * 5000,
        }
    )
    assembled = assembler.build(
        history=[
            ChatMessage(role="user", content="calc"),
            ChatMessage(
                role="tool",
                name="computer_get_state",
                content=heavy,
                tool_call_id="1",
            ),
        ],
        tools=[],
    )
    tool_msgs = [m for m in assembled.messages if m.role == "tool"]
    assert tool_msgs
    body = tool_msgs[-1].content or ""
    assert "42" in body
    assert "snapshot" not in body
    assert len(body) < len(heavy)
