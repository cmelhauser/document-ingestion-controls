#!/usr/bin/env bash
# Render the tracked LaTeX sources to project PDFs with enforced 1-inch Letter margins.
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

# Freeze release-document metadata so repeated renders are byte-identical.
export SOURCE_DATE_EPOCH=1786579200

if command -v tectonic >/dev/null 2>&1; then
  latex_engine="$(command -v tectonic)"
elif command -v brew >/dev/null 2>&1 && [ -x "$(brew --prefix tectonic)/bin/tectonic" ]; then
  latex_engine="$(brew --prefix tectonic)/bin/tectonic"
else
  echo "Tectonic is required. Install it with: brew install tectonic" >&2
  exit 1
fi

# The margin and table guards below are safety checks, so a missing search tool
# must fail loudly rather than be reported as a failed guard. `grep -F`/`grep -E`
# are equivalent here and are present on every supported platform.
if command -v rg >/dev/null 2>&1; then
  fixed_search() { rg -Fq "$1" "$2"; }
  regex_search() { rg -q "$1" "$2"; }
else
  fixed_search() { grep -Fq -- "$1" "$2"; }
  regex_search() { grep -Eq -- "$1" "$2"; }
fi

for source in \
  docs/TECHNICAL_DOCUMENTATION.tex \
  docs/CLIENT_OVERVIEW.tex \
  docs/CLIENT_OUTPUT_OVERVIEW.tex \
  docs/CLIENT_PROCESS_PLAIN_LANGUAGE.tex \
  docs/CLIENT_USER_GUIDE.tex \
  docs/BUSINESS_DOCUMENT_INGESTION_ANALYTICS_PLAN.tex; do
  if ! fixed_search '\usepackage[letterpaper,margin=1in]{geometry}' "$source"; then
    echo "Missing required 1-inch Letter geometry declaration: $source" >&2
    exit 1
  fi
  if regex_search '\\begin\{longtable\}.*@\{\}l+@\{\}' "$source"; then
    echo "Unbounded natural-width table columns would cross page margins: $source" >&2
    exit 1
  fi
  "$latex_engine" --outdir docs "$source"
done
