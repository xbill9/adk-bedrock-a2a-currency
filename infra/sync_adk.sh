#!/bin/bash
# Copy the framework-independent packages into the ADK agent directory so the
# Cloud Run source upload is self-contained (the buildpack cannot import from
# outside adk_agent/). Run before `gcloud run deploy`. The copies are build
# artifacts: edit the repo-root packages, never the copies.
#
# The coordinator needs mcp_server/ as well as coordinator/: its mcp_only
# baseline spawns `python -m mcp_server.server` over stdio.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENT_DIR="$REPO_ROOT/adk_agent"

for pkg in coordinator mcp_server; do
  rm -rf "$AGENT_DIR/$pkg"
  rsync -a --exclude '__pycache__' "$REPO_ROOT/$pkg/" "$AGENT_DIR/$pkg/"
done
echo "Synced coordinator/ and mcp_server/ into $AGENT_DIR"
