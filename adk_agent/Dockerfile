# Benchmark ADK master agent (A2A) for Cloud Run.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock agent.py start.sh ./
# Synced by infra/sync_adk.sh; the benchmark tool and the stdio MCP server.
COPY coordinator/ ./coordinator/
COPY mcp_server/ ./mcp_server/

RUN uv sync --frozen && chmod +x start.sh

# Cloud Run injects PORT for the A2A app.
# GOOGLE_API_KEY must come from Secret Manager at deploy time, not an env literal.
EXPOSE 8080

CMD ["/app/start.sh"]
