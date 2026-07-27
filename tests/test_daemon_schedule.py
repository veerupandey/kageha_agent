import time

from kageha.daemon.schedule import (
    ensure_consolidate_job,
    ensure_curator_job,
    load_schedule,
    run_tick,
    status_text,
    upsert_job,
)
from kageha.memory.service import reset_memory_service_for_tests


def test_ensure_and_tick_dry(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    job = ensure_curator_job(days=30)
    assert job.id == "curator"
    assert (home / "schedule.json").is_file()
    # Force due
    job.next_run = 0
    job.opts = {"days": 30, "dry_run": True}
    upsert_job(job)
    lines = run_tick()
    assert any("curator run" in line for line in lines)
    data = load_schedule()
    stored = next(j for j in data["jobs"] if j["id"] == "curator")
    assert stored["last_run"] > 0
    assert status_text()


def test_ensure_consolidate_and_tick(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    monkeypatch.setenv("KAGEHA_MEMORY_CONSOLIDATE_HOURS", "0")
    reset_memory_service_for_tests()
    job = ensure_consolidate_job()
    assert job.id == "consolidate"
    assert job.kind == "consolidate"
    job.next_run = 0
    upsert_job(job)
    lines = run_tick()
    assert any("consolidate" in line for line in lines)
    data = load_schedule()
    stored = next(j for j in data["jobs"] if j["id"] == "consolidate")
    assert stored["last_run"] > 0
    assert (home / "memory" / "MEMORY.md").is_file()


def test_tick_no_due(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    job = ensure_curator_job()
    job.next_run = time.time() + 10_000
    upsert_job(job)
    cons = ensure_consolidate_job()
    cons.next_run = time.time() + 10_000
    upsert_job(cons)
    lines = run_tick(force=False)
    assert lines == ["(no due jobs)"]
