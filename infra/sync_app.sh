#!/bin/bash
# Copy the framework-independent packages into the AgentCore app directory so
# the CodeZip bundle is self-contained (the runtime cannot import from outside
# app/CurrencyCoordinator/). Run before `agentcore deploy`. The copies are
# build artifacts: edit the repo-root packages, never the copies.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="$REPO_ROOT/app/CurrencyCoordinator"

for pkg in coordinator mcp_server; do
  rm -rf "$APP_DIR/$pkg"
  rsync -a --exclude '__pycache__' "$REPO_ROOT/$pkg/" "$APP_DIR/$pkg/"
done
echo "Synced coordinator/ and mcp_server/ into $APP_DIR"
