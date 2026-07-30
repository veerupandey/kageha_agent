"""Golden-task eval harness."""

from __future__ import annotations

import json
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from kageha.loop.controller import LoopController, RunResult


@dataclass
class GoldenTask:
    id: str
    prompt: str
    expect_files: list[str]
    expect_status: list[str] = field(default_factory=lambda: ["success"])
    expect_file_contains: dict[str, str] = field(default_factory=dict)
    min_file_bytes: dict[str, int] = field(default_factory=dict)
    expect_glob_counts: dict[str, int] = field(default_factory=dict)
    expect_image_dimensions: dict[str, list[int]] = field(default_factory=dict)
    expect_pdf_min_pages: dict[str, int] = field(default_factory=dict)
    expect_pptx_slides: dict[str, int] = field(default_factory=dict)
    max_usd: float = 0.5
    max_steps: int = 20
    # Additive contract-aware fields (REL-040, Requirement 16.1) — optional,
    # never change load_goldens()'s JSON shape for files that omit them.
    contract_criteria: list[dict[str, Any]] = field(default_factory=list)
    fixtures: dict[str, Any] = field(default_factory=dict)
    forbidden_actions: list[str] = field(default_factory=list)
    expected_terminal_state: str = "success"
    max_time_s: float = 300.0
    repeat: int = 1


def run_environment() -> dict[str, Any]:
    """Model identifier, harness config hash, dependency lock digest,
    platform, and repository commit for one evaluation run (Requirement 16.2).
    """
    import hashlib
    import json as _json
    import platform as _platform
    import subprocess

    from kageha.config import project_root

    model_id = ""
    try:
        from kageha.models.registry import ModelRegistry

        reg = ModelRegistry.load()
        model_id = str((reg.roles.get("default") or [""])[0])
    except Exception:  # noqa: BLE001
        model_id = ""

    harness_config = {"harness": "eval.harness", "version": 1}
    config_hash = hashlib.sha256(
        _json.dumps(harness_config, sort_keys=True).encode()
    ).hexdigest()[:16]

    lock_digest = ""
    for lock_name in ("uv.lock", "poetry.lock"):
        lock_path = project_root() / lock_name
        if lock_path.is_file():
            lock_digest = hashlib.sha256(lock_path.read_bytes()).hexdigest()[:16]
            break

    commit = ""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(project_root()),
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            commit = proc.stdout.strip()
    except Exception:  # noqa: BLE001
        commit = ""

    return {
        "model_id": model_id,
        "harness_config_hash": config_hash,
        "dependency_lock_digest": lock_digest,
        "platform": _platform.platform(),
        "commit": commit,
    }


@dataclass
class EvalResult:
    task_id: str
    passed: bool
    status: str
    steps: int
    spent_usd: float
    elapsed_s: float
    reasons: list[str]


async def run_golden(
    task: GoldenTask,
    *,
    auto_approve: bool = True,
    controller_factory: Callable[[], LoopController] | None = None,
) -> EvalResult:
    import os

    os.environ["KAGEHA_MAX_STEPS"] = str(task.max_steps)
    os.environ["KAGEHA_MAX_USD"] = str(task.max_usd)
    t0 = time.time()
    using_test_adapter = controller_factory is not None
    if controller_factory is not None:
        # Deterministic unit tests may inject a fake core-loop adapter. Release
        # evaluations always take the journal-backed path below.
        result: RunResult = await controller_factory().run(task.prompt)
    else:
        from kageha.config import security_profile
        from kageha.runtime import (
            AgentRuntime,
            SecurityProfile,
            TurnRequest,
        )

        runtime = AgentRuntime()
        try:
            result = await runtime.execute(
                TurnRequest(
                    objective=task.prompt,
                    auto_approve=auto_approve,
                    security_profile=SecurityProfile(security_profile()),
                    max_steps=task.max_steps,
                    project_root=str(Path.cwd()),
                    platform="eval",
                    live=False,
                )
            )
        finally:
            runtime.close()
    elapsed = time.time() - t0
    reasons = []
    passed = True
    # A golden task measures completion. Resource exhaustion and no-progress
    # states are never successes, even if an old suite listed them as acceptable.
    if result.status != "success":
        passed = False
        reasons.append(f"status {result.status}; required success")
    from kageha.harness.sandbox import SessionWorkspace

    workspace: SessionWorkspace | None = None

    def current_workspace() -> SessionWorkspace:
        nonlocal workspace
        if workspace is None:
            if using_test_adapter:
                workspace = SessionWorkspace.open(result.run_id)
            else:
                workspace = SessionWorkspace.create(result.run_id)
        return workspace

    for rel in task.expect_files:
        if rel not in result.artifacts and not any(a.endswith(rel) for a in result.artifacts):
            if not (current_workspace().root / rel).exists():
                passed = False
                reasons.append(f"missing file {rel}")
    if (
        task.expect_file_contains
        or task.min_file_bytes
        or task.expect_glob_counts
        or task.expect_image_dimensions
        or task.expect_pdf_min_pages
        or task.expect_pptx_slides
    ):
        current_workspace()
    for rel, expected in task.expect_file_contains.items():
        path = current_workspace().root / rel
        if not path.is_file():
            passed = False
            reasons.append(f"missing file {rel} for content check")
            continue
        if expected not in path.read_text(errors="replace"):
            passed = False
            reasons.append(f"file {rel} missing expected content")
    for rel, minimum in task.min_file_bytes.items():
        path = current_workspace().root / rel
        if not path.is_file():
            passed = False
            reasons.append(f"missing file {rel} for size check")
            continue
        actual = path.stat().st_size
        if actual < minimum:
            passed = False
            reasons.append(f"file {rel} has {actual} bytes; expected at least {minimum}")
    for pattern, expected in task.expect_glob_counts.items():
        actual = len(
            [
                path
                for path in current_workspace().root.glob(pattern)
                if path.is_file()
            ]
        )
        if actual != expected:
            passed = False
            reasons.append(f"glob {pattern} matched {actual} files; expected {expected}")
    for rel, expected in task.expect_image_dimensions.items():
        path = current_workspace().root / rel
        try:
            from PIL import Image

            with Image.open(path) as image:
                actual = [image.width, image.height]
        except Exception as exc:  # noqa: BLE001
            passed = False
            reasons.append(f"could not inspect image {rel}: {exc}")
            continue
        if actual != expected:
            passed = False
            reasons.append(f"image {rel} is {actual}; expected {expected}")
    for rel, minimum in task.expect_pdf_min_pages.items():
        path = current_workspace().root / rel
        try:
            from pypdf import PdfReader

            actual = len(PdfReader(str(path)).pages)
        except Exception as exc:  # noqa: BLE001
            passed = False
            reasons.append(f"could not inspect PDF {rel}: {exc}")
            continue
        if actual < minimum:
            passed = False
            reasons.append(f"PDF {rel} has {actual} pages; expected at least {minimum}")
    for rel, expected in task.expect_pptx_slides.items():
        path = current_workspace().root / rel
        try:
            with zipfile.ZipFile(path) as archive:
                actual = sum(
                    1
                    for name in archive.namelist()
                    if name.startswith("ppt/slides/slide")
                    and name.endswith(".xml")
                    and "/_rels/" not in name
                )
        except Exception as exc:  # noqa: BLE001
            passed = False
            reasons.append(f"could not inspect PPTX {rel}: {exc}")
            continue
        if actual != expected:
            passed = False
            reasons.append(f"PPTX {rel} has {actual} slides; expected {expected}")
    return EvalResult(
        task_id=task.id,
        passed=passed,
        status=result.status,
        steps=result.steps,
        spent_usd=result.spent_usd,
        elapsed_s=elapsed,
        reasons=reasons,
    )


def load_goldens(path: Path) -> list[GoldenTask]:
    data = json.loads(path.read_text())
    return [GoldenTask(**item) for item in data]


async def run_suite(path: Path, **kwargs: Any) -> list[EvalResult]:
    tasks = load_goldens(path)
    out = []
    for task in tasks:
        out.append(await run_golden(task, **kwargs))
    return out


def summary(results: list[EvalResult]) -> dict[str, Any]:
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "usd": sum(r.spent_usd for r in results),
        "results": [asdict(r) for r in results],
    }
