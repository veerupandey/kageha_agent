"""Followup mode must not false-succeed action claims without tools."""

from __future__ import annotations

from kageha.loop.goal_card import GoalCard, GoalItem
from kageha.loop.verifier import is_lookup_status_goal


def test_create_file_goal_is_not_informational():
    goal = GoalCard(
        task="Create a file named hello.txt containing exactly: hi",
        items=[
            GoalItem(id="g1", description="hello.txt exists with content hi"),
        ],
    )
    assert not is_lookup_status_goal(goal)


def test_status_question_goal_is_informational():
    goal = GoalCard(
        task="What is the current status of the deploy?",
        items=[
            GoalItem(id="g1", description="Report deploy status"),
        ],
    )
    assert is_lookup_status_goal(goal)
