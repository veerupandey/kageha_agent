"""GoalCard sync from todo.md checkboxes."""

from __future__ import annotations

from pathlib import Path

from kageha.loop.goal_card import GoalCard, GoalItem
from kageha.loop.todo_board import (
    apply_done_ids_to_todo_markdown,
    collect_todo_done_ids,
    parse_todo_markdown,
    sync_todo_file_from_progress,
)


def test_apply_todo_checkboxes_marks_checked_goals():
    goal = GoalCard(
        task="scan tvs",
        items=[
            GoalItem("g1", "Network scanned for TV devices"),
            GoalItem("g2", "List of available TVs obtained and reported"),
        ],
    )
    todo = """# Goal: scan tvs

- [x] `g1` Network scanned for TV devices
- [x] `g2` List of available TVs obtained and reported
"""
    n = goal.apply_todo_checkboxes(todo, evidence="network_tvs.md")
    assert n == 2
    assert goal.all_passed()
    assert goal.items[0].evidence == "network_tvs.md"
    assert goal.apply_todo_checkboxes(todo, evidence="again") == 0


def test_todo_board_parse_aligns_with_goal_checkbox_ids():
    todo = """# Goal: scan tvs

- [x] `g1` Network scanned for TV devices
- [ ] `g2` List of available TVs obtained and reported
"""
    board = parse_todo_markdown(todo, label="todos")
    assert board["done"] == 1
    assert board["total"] == 2
    assert [it["id"] for it in board["items"]] == ["g1", "g2"]
    assert board["items"][0]["done"] is True
    assert board["items"][1]["done"] is False


def test_sync_todo_file_from_goal_and_success_marks_plan(tmp_path: Path):
    todo = """# Plan

- [ ] p1: Inspect the workspace and gather inputs
- [ ] p2: Execute the core work toward the deliverable
- [ ] p3: Verify the result and write a short summary

# Goal: pitch deck

- [ ] `g1` Understood the task and constraints
- [ ] `g2` Produced the primary deliverable
- [ ] `g3` Verified the deliverable against the request
"""
    path = tmp_path / "todo.md"
    path.write_text(todo, encoding="utf-8")
    goal = GoalCard(
        task="pitch deck",
        items=[
            GoalItem("g1", "Understood the task and constraints", passes=True),
            GoalItem("g2", "Produced the primary deliverable", passes=True),
            GoalItem("g3", "Verified the deliverable against the request", passes=True),
        ],
    )
    assert sync_todo_file_from_progress(path, goal=goal, success=True)
    board = parse_todo_markdown(path.read_text(encoding="utf-8"))
    assert board["done"] == 6
    assert board["total"] == 6


def test_apply_done_ids_preserves_unchecked():
    md = "- [ ] p1: step one\n- [x] `g1` already done\n"
    new_md, changed = apply_done_ids_to_todo_markdown(md, {"p1", "g2"})
    assert changed
    assert "- [x] p1:" in new_md
    assert "- [x] `g1`" in new_md
    assert collect_todo_done_ids(goal=GoalCard(task="t", items=[GoalItem("g1", "a", passes=True)])) == {
        "g1"
    }
