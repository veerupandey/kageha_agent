from datetime import datetime, timedelta, timezone

from kageha.memory.curator import (
    archive_skill,
    is_pinned,
    record_skill_use,
    restore_skill,
    run_curator,
    set_pinned,
    status_rows,
)
from kageha.memory.skills import SkillRegistry


def test_record_use_and_pin_blocks_delete(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    reg = SkillRegistry()
    reg.create_stub("curated-demo", "demo")
    body = reg.load_body("curated-demo")
    assert "curated-demo" in body
    usage = (home / "skills" / "usage.json").read_text()
    assert "curated-demo" in usage
    assert '"loads": 1' in usage

    assert set_pinned("curated-demo", True).startswith("Pinned")
    assert is_pinned("curated-demo")
    out = reg.manage("delete", "curated-demo", approved=True)
    assert "pinned" in out.lower()
    assert "curated-demo" in SkillRegistry().skills

    set_pinned("curated-demo", False)
    out = reg.manage("delete", "curated-demo", approved=True)
    assert out.startswith("Deleted")


def test_archive_restore_and_dry_run(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    SkillRegistry().create_stub("stale-skill", "old")
    record_skill_use("stale-skill")
    # Force stale by rewriting last_used
    import json

    path = home / "skills" / "usage.json"
    data = json.loads(path.read_text())
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    data["stale-skill"]["last_used"] = old
    data["stale-skill"]["created"] = old
    path.write_text(json.dumps(data))

    dry = run_curator(days=30, dry_run=True)
    assert any("DRY-RUN" in line for line in dry)
    assert (home / "skills" / "stale-skill" / "SKILL.md").is_file()

    live = run_curator(days=30, dry_run=False)
    assert any("Archived" in line for line in live)
    assert not (home / "skills" / "stale-skill" / "SKILL.md").is_file()
    assert (home / "skills" / "archive" / "stale-skill" / "SKILL.md").is_file()

    assert restore_skill("stale-skill").startswith("Restored")
    assert (home / "skills" / "stale-skill" / "SKILL.md").is_file()


def test_pin_skips_archive(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    SkillRegistry().create_stub("keep-me", "keep")
    set_pinned("keep-me", True)
    import json

    path = home / "skills" / "usage.json"
    data = json.loads(path.read_text())
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    data["keep-me"]["last_used"] = old
    data["keep-me"]["created"] = old
    path.write_text(json.dumps(data))
    out = archive_skill("keep-me")
    assert out.startswith("SKIP")
    rows = status_rows(stale_days=30)
    keep = next(r for r in rows if r.name == "keep-me")
    assert keep.pinned
    assert not keep.archived
