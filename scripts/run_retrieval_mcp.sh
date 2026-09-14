#!/usr/bin/env bash
# Launch the local, read-only business-document retrieval MCP server.
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: bash scripts/run_retrieval_mcp.sh [RETRIEVAL_DATABASE]

Launch the local stdio MCP server for one immutable approved-fact SQLite snapshot.

Arguments:
  RETRIEVAL_DATABASE  Snapshot built by retrieval_store.py build. Defaults to
                      BUSINESS_DOCUMENT_RETRIEVAL_DB, then
                      ./business_retrieval.sqlite under the repository root.

Environment:
  BUSINESS_DOCUMENT_RETRIEVAL_DB  Default snapshot path when no argument is given.
  PYTHON_BIN                     Python executable; defaults to .venv/bin/python.

The launcher is local and read-only. Complete the Phase 6 activation gate in
references/mcp-production-integration.md before registering it with a client.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
if (( $# > 1 )); then
  usage >&2
  exit 2
fi

database_path="${1:-${BUSINESS_DOCUMENT_RETRIEVAL_DB:-$repository_root/business_retrieval.sqlite}}"
python_bin="${PYTHON_BIN:-$repository_root/.venv/bin/python}"

if [[ ! -f "$database_path" ]]; then
  echo "Retrieval database not found: $database_path" >&2
  echo "Build it first with scripts/retrieval_store.py build." >&2
  exit 2
fi
if [[ ! -x "$python_bin" ]]; then
  python_bin="$(command -v python3 || true)"
fi
if [[ -z "$python_bin" ]]; then
  echo "Python 3 is required. Create .venv using README.md first." >&2
  exit 2
fi
exec "$python_bin" "$repository_root/scripts/retrieval_mcp.py" "$database_path"
