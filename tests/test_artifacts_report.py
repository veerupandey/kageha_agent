from kageha.harness.sandbox import SessionWorkspace
from kageha.loop.artifacts import (
    artifact_delta,
    artifacts_mentioned_in_text,
    artifacts_touched_since,
    classify_artifacts,
    format_artifacts_compact,
    format_artifacts_report,
    humanize_turn_reply,
    is_user_artifact,
    mirror_deliverables_into_session,
    snapshot_artifact_mtimes,
)


def test_filters_internal_files():
    paths = [
        "todo.md",
        "goal_card.json",
        "events.jsonl",
        "checkpoints/LATEST.md",
        "_memory/x.md",
        "artifacts/slide_01.jpg",
        "artifacts/video_frames/seg_01.mp4",
        ".DS_Store",
        "research/brief.md",
        "inputs/slides.md",
    ]
    out = classify_artifacts(paths)
    assert "artifacts/slide_01.jpg" in out
    assert "research/brief.md" in out
    assert "inputs/slides.md" not in out
    assert "todo.md" not in out
    assert "checkpoints/LATEST.md" not in out
    assert "artifacts/video_frames/seg_01.mp4" not in out
    assert ".DS_Store" not in out
    assert out[0].startswith("artifacts/")


def test_format_report_includes_abs_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    text = format_artifacts_report(
        run_id="abc123",
        artifacts=["artifacts/deck.html", "todo.md"],
        workspace_root=tmp_path / "sessions" / "abc123",
    )
    assert "Artifacts" in text
    assert "artifacts/deck.html" in text
    assert "todo.md" not in text


def test_highlight_new_this_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    before = ["artifacts/old.png"]
    after = ["artifacts/old.png", "artifacts/dog.png", "todo.md"]
    new = artifact_delta(before, after)
    assert new == ["artifacts/dog.png"]
    text = format_artifacts_report(
        run_id="r1",
        artifacts=after,
        workspace_root=tmp_path / "sessions" / "r1",
        highlight=new,
        older_count=1,
    )
    assert "New this turn" in text
    assert "dog.png" in text
    assert "old.png" not in text
    assert "earlier" in text


def test_humanize_stop_jargon(tmp_path):
    reply = humanize_turn_reply(
        message="Goals validated with evidence",
        status="success",
        user_line="create an image of a dog",
        new_artifacts=["artifacts/dog_dancing_in_rain.png"],
        workspace_root=tmp_path,
    )
    assert "Goals validated" not in reply
    assert "dog_dancing_in_rain.png" in reply
    assert str(tmp_path / "artifacts/dog_dancing_in_rain.png") in reply


def test_touched_includes_overwritten_files(tmp_path):
    arts = tmp_path / "artifacts"
    arts.mkdir()
    target = arts / "browse.png"
    target.write_bytes(b"old")
    before = snapshot_artifact_mtimes(tmp_path, ["artifacts/browse.png"])
    import time

    time.sleep(0.02)
    target.write_bytes(b"new-screenshot-bytes")
    touched = artifacts_touched_since(
        tmp_path, ["artifacts/browse.png"], before
    )
    assert "artifacts/browse.png" in touched


def test_mentioned_paths_count_as_touched(tmp_path):
    arts = tmp_path / "artifacts"
    arts.mkdir()
    (arts / "browse.png").write_bytes(b"same")
    before = snapshot_artifact_mtimes(tmp_path, ["artifacts/browse.png"])
    # Same size/mtime — but model named the path in its reply
    touched = artifacts_touched_since(
        tmp_path,
        ["artifacts/browse.png"],
        before,
        also_mention="Screenshot saved to `artifacts/browse.png`",
    )
    assert "artifacts/browse.png" in touched
    assert artifacts_mentioned_in_text(
        "saved to artifacts/browser_open_120102.png"
    ) == ["artifacts/browser_open_120102.png"]


def test_failed_run_never_claims_partial_artifact_is_done(tmp_path):
    reply = humanize_turn_reply(
        message="Hit max steps (15)",
        status="max_steps",
        user_line="Browse LinkedIn and show the profile",
        new_artifacts=["artifacts/search_notes.md"],
        workspace_root=tmp_path,
    )
    assert "couldn't complete" in reply
    assert "Partial files" in reply
    assert "Done" not in reply


def test_error_hides_flattened_tool_call_junk(tmp_path):
    reply = humanize_turn_reply(
        message="[called tools: bash] bash(['command'])",
        status="error",
        user_line="make it polished",
        new_artifacts=[],
        workspace_root=tmp_path,
    )
    assert "couldn't complete" in reply
    assert "bash(['command'])" not in reply
    assert "provider/routing" in reply.lower() or "try again" in reply.lower()


def test_no_progress_surfaces_substantive_grace_answer(tmp_path):
    answer = (
        "### Discovered TV\n"
        "- **Device**: Sony Bravia TV\n"
        "- **IP Address**: `10.0.0.14`\n"
        "- **Power State**: Active\n"
        "The network scan found one TV on the LAN."
    )
    reply = humanize_turn_reply(
        message=answer,
        status="no_progress",
        user_line="check what tv is available in network",
        new_artifacts=["network_tvs.md"],
        workspace_root=tmp_path,
    )
    assert "10.0.0.14" in reply
    assert "couldn't complete" not in reply
    assert "Partial files" not in reply


def test_root_source_file_is_not_a_user_artifact():
    assert not is_user_artifact("search.py")
    assert not is_user_artifact("scripts/search.py")
    assert is_user_artifact("outputs/search.py")


def test_successful_browse_surfaces_result_evidence(tmp_path):
    reply = humanize_turn_reply(
        message="Goals validated with evidence",
        status="success",
        user_line="show the LinkedIn profile here",
        new_artifacts=[],
        workspace_root=tmp_path,
        result_evidence=(
            "Rakesh Pandey — Senior AI Engineer | ICBC — "
            "https://ca.linkedin.com/in/rakeshpandey820"
        ),
    )
    assert "Found and verified" in reply
    assert "rakeshpandey820" in reply
    assert "Done for:" not in reply


def test_pending_question_is_returned_verbatim(tmp_path):
    reply = humanize_turn_reply(
        message="Use the professional style?\n\n[Y] Yes\n[N] No",
        status="ask_user",
        user_line="make these better",
        new_artifacts=[],
        workspace_root=tmp_path,
    )
    assert reply.startswith("Use the professional style?")
    assert "[Y] Yes" in reply


def test_compact_empty():
    assert "none" in format_artifacts_compact(run_id="x", artifacts=[]).lower()
    assert is_user_artifact("artifacts/a.png")
    assert not is_user_artifact("plan.json")
    assert not is_user_artifact("artifacts/video_frames/x.mp4")
    assert not is_user_artifact("_turns/abc123.json")
    assert not is_user_artifact(
        "artifacts/bridges/whatsapp-baileys/node_modules/.bin/pino"
    )
    assert not is_user_artifact("artifacts/src/kageha/__pycache__/x.pyc")


def test_mirror_skips_dependency_trees(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    project = tmp_path / "proj"
    nm = project / "bridges" / "whatsapp-baileys" / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    junk = nm / "index.js"
    junk.write_text("module.exports = {}\n", encoding="utf-8")
    good = project / "artifacts" / "shot.png"
    good.parent.mkdir(parents=True)
    good.write_bytes(b"png")

    ws = SessionWorkspace.create("sess-mirror-noise")
    mirrored = mirror_deliverables_into_session(
        ws,
        source_root=project,
        relative_paths={
            "bridges/whatsapp-baileys/node_modules/pkg/index.js",
            "artifacts/shot.png",
        },
    )
    assert mirrored == ["artifacts/shot.png"]
    assert not ws.path(
        "artifacts/bridges/whatsapp-baileys/node_modules/pkg/index.js"
    ).is_file()
    assert ws.path("artifacts/shot.png").is_file()


def test_project_change_detection_respects_skip_dirs(tmp_path):
    from kageha.loop.controller import (
        _PROJECT_SNAPSHOT_SKIP,
        _changed_workspace_paths,
        _workspace_file_snapshot,
    )

    root = tmp_path / "proj"
    (root / "artifacts").mkdir(parents=True)
    (root / "artifacts" / "ok.png").write_bytes(b"png")
    nm = root / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("1\n", encoding="utf-8")

    before = _workspace_file_snapshot(root, skip_dir_names=_PROJECT_SNAPSHOT_SKIP)
    (nm / "index.js").write_text("2\n", encoding="utf-8")
    (root / "artifacts" / "ok.png").write_bytes(b"png2")
    changed = _changed_workspace_paths(
        root, before, skip_dir_names=_PROJECT_SNAPSHOT_SKIP
    )
    assert "artifacts/ok.png" in changed
    assert not any("node_modules" in p for p in changed)


def test_mirror_deliverables_into_session(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    project = tmp_path / "proj"
    project.mkdir()
    deck = project / "bare_fair_investor_pitch.pptx"
    deck.write_bytes(b"PK-pptx")
    (project / "create_pitch_deck.py").write_text("print('ok')\n", encoding="utf-8")

    ws = SessionWorkspace.create("sess-mirror")
    mirrored = mirror_deliverables_into_session(
        ws,
        source_root=project,
        relative_paths={"bare_fair_investor_pitch.pptx", "create_pitch_deck.py"},
    )
    assert mirrored == ["artifacts/bare_fair_investor_pitch.pptx"]
    assert ws.path("artifacts/bare_fair_investor_pitch.pptx").is_file()
    assert ws.path("artifacts/bare_fair_investor_pitch.pptx").read_bytes() == b"PK-pptx"
