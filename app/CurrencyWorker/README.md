# Currency worker (Bedrock AgentCore, A2A)

The AWS half of the benchmark: a Strands agent on a Bedrock model, exposed as
an **A2A v1.0 server** and invoked by the Google ADK master on Cloud Run. It
answers with one JSON quote object per line; it makes no outbound agent calls
of its own.

Scaffolded by the AgentCore CLI, then reworked when the topology was reversed
on 2026-07-30 — the notes below reflect what it actually is now, not the
generated defaults.

## Layout

- `main.py` — the A2A server. It builds the agent card, JSON-RPC routes, and
  request handler from `a2a-sdk` directly, wrapping a Strands agent in an
  `AgentExecutor`. There is no `@app.entrypoint`: that decorator belongs to the
  `HTTP` protocol, and this runtime is deployed with `"protocol": "A2A"`.
- `model/load.py` — instantiates the Bedrock model (Nova Micro by default).
- `coordinator/`, `mcp_server/` — copies of the repo-root packages synced by
  `infra/sync_app.sh`. Build artifacts, gitignored; edit the root packages.

Project configuration lives in the repo-root `agentcore/` folder, not here.

## Why not `strands-agents[a2a]`

That extra pins `a2a-sdk<0.4`, i.e. the A2A **v0.3** wire methods, even at the
latest release. The ADK master uses `a2a-sdk` 1.x (**v1.0**), and the two cannot
interoperate — there is no version negotiation. Strands is therefore used only
as the agent loop, and the v1.0 server surface is assembled in `main.py`.
`tests/test_deployment_config.py` asserts the lockfile keeps `a2a-sdk` on 1.x.

## Ports and auth

AgentCore's A2A runtime proxies JSON-RPC to the container on **port 9000** and
expects the agent card at `/.well-known/agent-card.json`.

Inbound requests are authorized *before* they reach this process, by AgentCore's
default `AWS_IAM` authorizer: callers must SigV4-sign with credentials for a
role permitted to invoke this runtime. The coordinator gets those by federating
its Google identity through STS; see `docs/ARCHITECTURE.md`. No credential
handling happens in this code.

## Local development

```bash
./infra/sync_app.sh          # from the repo root
cd app/CurrencyWorker && uv sync
uv run python main.py        # serves A2A on 0.0.0.0:9000, unauthenticated
```

Locally there is no authorizer in front, so any client can call it:

```bash
curl http://127.0.0.1:9000/.well-known/agent-card.json
currency-benchmark 100 USD EUR --mode a2a_only --a2a-endpoint http://127.0.0.1:9000
```

## Deployment

See `infra/README.md`. `agentcore invoke` is not useful against the deployed
runtime: it speaks JSON-RPC under `"protocol": "A2A"`. Drive the loop through
the coordinator instead.
