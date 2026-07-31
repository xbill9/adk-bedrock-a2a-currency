#!/bin/sh
# Cloud Run entry point: colocated MCP rate server + A2A agent.
set -e
/app/.venv/bin/python mcp_server.py &
sleep 2
exec /app/.venv/bin/uvicorn agent:a2a_app --host 0.0.0.0 --port "${PORT:-8080}"
