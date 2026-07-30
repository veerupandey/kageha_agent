#!/usr/bin/env bash
# Core_Qualification_Command (REL-003, Requirements 4.1, 4.5).
#
# Fast, representative subset of the qualification checks for quick local
# iteration: lint plus the Python test suite. It intentionally skips the
# slower type-check and frontend-test steps that only the
# Full_Qualification_Command (see docs/USAGE.md → Development) is required
# to run together. Unlike the Full_Qualification_Command, this command is
# not required to suppress interactive prompting for individual checks.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "== Core_Qualification_Command: ruff =="
uv run ruff check .

echo "== Core_Qualification_Command: pytest =="
uv run pytest -q
