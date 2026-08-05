"""Tests for Cursor-style session auto-title helpers."""

from __future__ import annotations

from kageha.session_title import (
    TITLE_SOURCE_AUTO,
    TITLE_SOURCE_USER,
    apply_session_title,
    is_weak_title,
    pick_best_title,
    title_from_artifact_path,
    title_score,
    title_from_message,
)


def test_weak_titles():
    assert is_weak_title("hey")
    assert is_weak_title("P")
    assert is_weak_title("ok")
    assert is_weak_title("/status")
    assert not is_weak_title("KAGEHA Classic Ceremonial ad")
    assert not is_weak_title("Explain quantum entanglement")


def test_artifact_title():
    assert title_from_artifact_path("artifacts/market_research.md") == "Market Research"
    assert title_from_artifact_path("artifacts/The Daily Film Edit.png") == (
        "The Daily Film Edit"
    )
    assert title_from_artifact_path("artifacts/SKILL.md") is None


def test_message_title_uses_first_few_words():
    assert title_from_message(
        "Please install Factory desktop and configure the provider with my API key"
    ) == "Please install Factory desktop and configure the provider…"


def test_pick_best_prefers_substantive_user_over_greeting():
    title = pick_best_title(
        user_message="hey",
        assistant_message="Working on your request.",
        artifact_paths=["artifacts/market_research.md"],
    )
    assert title == "Market Research"
    assert title_score(title) > title_score("hey")


def test_apply_upgrades_weak_auto_title():
    meta = {"title": "hey", "title_source": TITLE_SOURCE_AUTO}
    meta, changed = apply_session_title(
        meta, candidate="KAGEHA Classic Ceremonial ad"
    )
    assert changed
    assert meta["title"] == "KAGEHA Classic Ceremonial ad"
    assert meta["title_source"] == TITLE_SOURCE_AUTO


def test_apply_does_not_overwrite_user_title():
    meta = {"title": "My campaign", "title_source": TITLE_SOURCE_USER}
    meta, changed = apply_session_title(
        meta, candidate="Something else entirely"
    )
    assert not changed
    assert meta["title"] == "My campaign"


def test_apply_force_user_rename():
    meta = {"title": "hey", "title_source": TITLE_SOURCE_AUTO}
    meta, changed = apply_session_title(
        meta, candidate="Pinned name", force_user=True
    )
    assert changed
    assert meta["title"] == "Pinned name"
    assert meta["title_source"] == TITLE_SOURCE_USER


def test_strong_auto_title_stays_stable():
    meta = {
        "title": "KAGEHA Classic Ceremonial ad",
        "title_source": TITLE_SOURCE_AUTO,
    }
    meta, changed = apply_session_title(meta, candidate="another follow-up question")
    assert not changed
    assert meta["title"] == "KAGEHA Classic Ceremonial ad"
