#!/bin/sh
# Cloud Run entry point: the ADK master agent served over A2A.
# The MCP rate server is no longer colocated as an HTTP service; the benchmark
# tool spawns `python -m mcp_server.server` over stdio per request.
set -e
exec /app/.venv/bin/uvicorn agent:a2a_app --host 0.0.0.0 --port "${PORT:-8080}"
