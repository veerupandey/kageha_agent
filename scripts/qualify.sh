#!/usr/bin/env bash
set -euo pipefail

runs="${1:-20}"
for ((i = 1; i <= runs; i++)); do
  echo "qualification run ${i}/${runs}"
  uv run --locked --all-extras ruff check .
  uv run --locked --all-extras pyright
  uv run --locked --all-extras pytest -q
done
