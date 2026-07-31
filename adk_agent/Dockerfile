# Benchmark ADK A2A agent + colocated MCP rate server for Cloud Run.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock agent.py mcp_server.py start.sh ./

RUN uv sync --frozen && chmod +x start.sh

# Cloud Run injects PORT for the A2A app; the MCP server stays on localhost:8081.
# GOOGLE_API_KEY must come from Secret Manager at deploy time, not an env literal.
EXPOSE 8080

CMD ["/app/start.sh"]
