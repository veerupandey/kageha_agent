#!/usr/bin/env bash
# Full_Qualification_Command (REL-003, Requirements 4.2, 4.3, 4.6).
#
# The all-extras qualification command: Python lint, Python type checking,
# the Python test suite, and the frontend test suite, run together. Unlike
# an individual check, this command must complete without prompting for
# input (except where an individual test genuinely requires confirmation to
# proceed). Pass a run count (default 1) to repeat the full sequence, e.g.
# `scripts/qualify.sh 3` for a release-gate stability check.
#
# Non-interactive guarantees:
#   - uv --locked: no lockfile update prompts
#   - pytest -m "not live_provider and not live_ui": excludes tests that
#     require live provider/UI interaction (they self-skip anyway via env
#     checks, but the marker deselection makes the intent explicit)
#   - pytest -rs: surfaces skip reasons in the short summary so that
#     skipped tests and their required environments are recorded
#     (REL-003, Requirement 4.4)
#   - The autouse stdin_guard fixture (conftest.py) fails any test that
#     attempts a blocking stdin read without a prior interactive prompt
#   - vitest run: non-watch, non-interactive mode
#   - npm ci: deterministic, no prompts
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

frontend_dir="src/kageha/webui/frontend"
runs="${1:-1}"

for ((i = 1; i <= runs; i++)); do
  echo "== Full_Qualification_Command: run ${i}/${runs} =="

  echo "-- lint (python) --"
  uv run --locked --all-extras ruff check .

  echo "-- type check (python) --"
  uv run --locked --all-extras pyright

  echo "-- python tests --"
  uv run --locked --all-extras pytest -q -rs -m "not live_provider and not live_ui"

  if [[ -f "${frontend_dir}/package.json" ]]; then
    if [[ ! -d "${frontend_dir}/node_modules" ]]; then
      echo "-- frontend: installing dependencies --"
      npm --prefix "${frontend_dir}" ci
    fi

    echo "-- lint (frontend) --"
    npm --prefix "${frontend_dir}" run lint

    echo "-- type check (frontend) --"
    npm --prefix "${frontend_dir}" run build

    echo "-- frontend tests --"
    npm --prefix "${frontend_dir}" test
  fi
done
