#!/usr/bin/env bash
# Generate tracked LaTeX from canonical Markdown with release layout safeguards.
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

if ! command -v pandoc >/dev/null 2>&1; then
  echo "Pandoc is required to regenerate tracked LaTeX documents." >&2
  exit 1
fi

for source in \
  docs/TECHNICAL_DOCUMENTATION.md \
  docs/CLIENT_OVERVIEW.md \
  docs/CLIENT_OUTPUT_OVERVIEW.md \
  docs/CLIENT_PROCESS_PLAIN_LANGUAGE.md \
  docs/CLIENT_USER_GUIDE.md \
  docs/BUSINESS_DOCUMENT_INGESTION_ANALYTICS_PLAN.md; do
  output="${source%.md}.tex"
  source_sha256="$(shasum -a 256 "$source" | awk '{print $1}')"
  pandoc "$source" \
    --standalone \
    --to latex \
    --variable geometry:letterpaper,margin=1in \
    --lua-filter scripts/pandoc_bounded_tables.lua \
    --include-in-header assets/pandoc-release-header.tex \
    --output "$output"
  SOURCE_SHA256="$source_sha256" perl -pi -e \
    'if ($. == 1) { print "% source-sha256: $ENV{SOURCE_SHA256}\n" }' "$output"
done
