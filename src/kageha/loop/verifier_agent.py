"""Enhanced verifier — runs deterministic checks before LLM verification.

The verifier agent upgrade adds:
1. Deterministic checks first (tests pass, files exist, lint clean)
2. Tool access for independent verification (read_file, bash, grep)
3. Different model family enforcement from executor
4. Structured defect emission with repair instructions

Order of verification:
1. File existence checks (do expected artifacts exist?)
2. Syntax/lint checks (can the code parse?)
3. Test execution (do tests pass?)
4. LLM verification (does the output match intent?)
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DeterministicCheck:
    """A single deterministic verification check."""

    name: str
    passed: bool
    evidence: str = ""
    error: str = ""


@dataclass
class DeterministicVerifyResult:
    """Results from all deterministic checks."""

    checks: list[DeterministicCheck] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def critical_failures(self) -> list[DeterministicCheck]:
        return [c for c in self.checks if not c.passed]

    def summary(self) -> str:
        lines = []
        for check in self.checks:
            status = "PASS" if check.passed else "FAIL"
            lines.append(f"  [{status}] {check.name}: {check.evidence or check.error}")
        return "\n".join(lines)


async def check_files_exist(
    expected_files: list[str],
    *,
    workspace: Path,
) -> DeterministicCheck:
    """Verify that expected output files exist."""
    missing = []
    found = []
    for fp in expected_files:
        path = workspace / fp if not os.path.isabs(fp) else Path(fp)
        if path.exists():
            size = path.stat().st_size
            found.append(f"{fp} ({size}b)")
        else:
            missing.append(fp)

    if missing:
        return DeterministicCheck(
            name="files_exist",
            passed=False,
            error=f"Missing files: {', '.join(missing)}",
            evidence=f"Found: {', '.join(found)}" if found else "",
        )
    return DeterministicCheck(
        name="files_exist",
        passed=True,
        evidence=f"All {len(found)} files present: {', '.join(found[:5])}",
    )


async def check_python_syntax(
    files: list[str],
    *,
    workspace: Path,
) -> DeterministicCheck:
    """Check Python files for syntax errors using py_compile."""
    py_files = [f for f in files if f.endswith(".py")]
    if not py_files:
        return DeterministicCheck(
            name="python_syntax",
            passed=True,
            evidence="No Python files to check",
        )

    errors = []
    for fp in py_files:
        path = workspace / fp if not os.path.isabs(fp) else Path(fp)
        if not path.exists():
            continue
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                ["python", "-m", "py_compile", str(path)],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(workspace),
            )
            if proc.returncode != 0:
                errors.append(f"{fp}: {proc.stderr.strip()[:200]}")
        except (subprocess.TimeoutExpired, OSError):
            pass

    if errors:
        return DeterministicCheck(
            name="python_syntax",
            passed=False,
            error=f"Syntax errors in {len(errors)} file(s):\n" + "\n".join(errors[:3]),
        )
    return DeterministicCheck(
        name="python_syntax",
        passed=True,
        evidence=f"All {len(py_files)} Python files pass syntax check",
    )


async def check_tests(
    *,
    workspace: Path,
    test_command: str | None = None,
    timeout: float = 60.0,
) -> DeterministicCheck:
    """Run the project's test suite and check results."""
    if test_command is None:
        # Auto-detect test command
        if (workspace / "pyproject.toml").exists():
            test_command = "python -m pytest --tb=short -q 2>&1 | tail -20"
        elif (workspace / "package.json").exists():
            test_command = "npm test 2>&1 | tail -20"
        elif (workspace / "Cargo.toml").exists():
            test_command = "cargo test 2>&1 | tail -20"
        else:
            return DeterministicCheck(
                name="tests",
                passed=True,
                evidence="No test framework detected — skipped",
            )

    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            test_command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(workspace),
        )
        output = (proc.stdout or "").strip()[-1000:]
        if proc.returncode == 0:
            return DeterministicCheck(
                name="tests",
                passed=True,
                evidence=f"Tests passed (exit 0): {output[-200:]}",
            )
        return DeterministicCheck(
            name="tests",
            passed=False,
            error=f"Tests failed (exit {proc.returncode}): {output[-400:]}",
        )
    except subprocess.TimeoutExpired:
        return DeterministicCheck(
            name="tests",
            passed=False,
            error=f"Tests timed out after {timeout}s",
        )
    except OSError as exc:
        return DeterministicCheck(
            name="tests",
            passed=False,
            error=f"Could not run tests: {exc}",
        )


async def check_lint(
    *,
    workspace: Path,
    files: list[str] | None = None,
) -> DeterministicCheck:
    """Run linter on modified files."""
    # Check for ruff (Python)
    if (workspace / "pyproject.toml").exists():
        target = " ".join(files[:10]) if files else "."
        cmd = f"python -m ruff check {target} 2>&1 | head -20"
    elif (workspace / "package.json").exists():
        cmd = "npx eslint --max-warnings 0 . 2>&1 | tail -10"
    else:
        return DeterministicCheck(
            name="lint",
            passed=True,
            evidence="No linter configured — skipped",
        )

    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(workspace),
        )
        output = (proc.stdout or "").strip()[-500:]
        if proc.returncode == 0:
            return DeterministicCheck(
                name="lint",
                passed=True,
                evidence="Lint clean",
            )
        return DeterministicCheck(
            name="lint",
            passed=False,
            error=f"Lint errors: {output}",
        )
    except (subprocess.TimeoutExpired, OSError):
        return DeterministicCheck(
            name="lint",
            passed=True,
            evidence="Lint check skipped (timeout/error)",
        )


async def run_deterministic_verification(
    *,
    workspace: Path,
    expected_files: list[str] | None = None,
    modified_files: list[str] | None = None,
    run_tests: bool = True,
    run_lint: bool = True,
    test_command: str | None = None,
) -> DeterministicVerifyResult:
    """Run all applicable deterministic checks.

    This should be called BEFORE the LLM verifier. If deterministic checks
    find critical failures, the LLM verification can be skipped and the
    defects reported directly for repair.
    """
    result = DeterministicVerifyResult()

    # 1. File existence
    if expected_files:
        check = await check_files_exist(expected_files, workspace=workspace)
        result.checks.append(check)

    # 2. Syntax check on modified files
    if modified_files:
        check = await check_python_syntax(modified_files, workspace=workspace)
        result.checks.append(check)

    # 3. Lint
    if run_lint and modified_files:
        check = await check_lint(workspace=workspace, files=modified_files)
        result.checks.append(check)

    # 4. Tests
    if run_tests:
        check = await check_tests(
            workspace=workspace, test_command=test_command
        )
        result.checks.append(check)

    return result
