#!/usr/bin/env bash
# Run the complete repository quality gate without an interactive-output deadline.
set -euo pipefail

python_bin="${PYTHON_BIN:-python3}"
quality_tmp_dir="$(mktemp -d)"
trap 'rm -rf "$quality_tmp_dir"' EXIT

"$python_bin" -m pip check
# One instrumented run, not two. The suite was executed twice -- once bare and
# once under coverage -- over the same 994 tests, so half of every gate, in CI
# and on every developer machine, was a duplicate of the other half. `coverage
# run` reports an identical pass/fail result; only the timing differs.
COVERAGE_FILE="$quality_tmp_dir/.coverage" PYTEST_ADDOPTS="-p no:cacheprovider" \
  "$python_bin" -m coverage run --branch -m pytest
COVERAGE_FILE="$quality_tmp_dir/.coverage" \
  "$python_bin" -m coverage report --fail-under=100
RUFF_CACHE_DIR="$quality_tmp_dir/ruff-cache" "$python_bin" -m ruff check .
RUFF_CACHE_DIR="$quality_tmp_dir/ruff-cache" \
  "$python_bin" -m ruff format --check scripts tests
"$python_bin" scripts/release_check.py
