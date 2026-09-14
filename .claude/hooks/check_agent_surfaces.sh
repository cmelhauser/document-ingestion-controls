#!/usr/bin/env bash
# Claude Code PostToolUse hook: keep operating instructions in step with the code.
#
# Fires after an edit to a file under scripts/. If the change renamed or removed a
# script that an agent surface still tells an operator to run, the instructions
# are now wrong and nothing else would catch it. Advisory: it reports, and never
# blocks or rewrites a change.
set -uo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repository_root" || exit 0

python_bin="${PYTHON_BIN:-}"
if [ -z "$python_bin" ]; then
  if [ -x ".venv/bin/python" ]; then
    python_bin=".venv/bin/python"
  else
    python_bin="$(command -v python3 || true)"
  fi
fi
[ -n "$python_bin" ] || exit 0

report="$("$python_bin" scripts/agent_surface_check.py --quiet --out /dev/stdout 2>&1)" || {
  printf '%s\n' "$report" >&2
  echo "Agent operating instructions no longer match scripts/. Update the skill folder and the command-line reference." >&2
  exit 0
}
exit 0
