# Google ADK master agent

The coordinator half of the benchmark: a Gemini `LlmAgent` that owns the three
benchmark modes, calls the MCP exchange-rate tool directly, and delegates
independent verification to the Strands worker on Bedrock AgentCore over A2A
v1.0. It is served over A2A itself (`to_a2a`) so the evaluation runner can drive
it the same way any A2A client would.

Derived from `xbill9/currency-agent@aeef3c4`, with the A2UI extension removed.
See the "A2A version skew" findings below for why.

`coordinator/` and `mcp_server/` in this directory are copies of the repo-root
packages, synced by `infra/sync_adk.sh`. They are build artifacts and are
gitignored — edit the root packages.

## Recorded interoperability findings

These are the reason the code looks the way it does. All still apply.

- **A2A v0.3 vs v1.0 is a hard split with no negotiation.** `MethodNotFoundError`
  is the observable symptom: a2a-sdk 1.x clients call `SendMessage`; 0.3.x
  servers only route `message/send`. The agent card offers no version
  negotiation. This affects any v1.0 client the same way, regardless of hosting
  cloud.
  - It forced A2UI out of this agent: `a2ui-agent-sdk` (through 0.4.0) pins
    `a2a-sdk<0.4`. Upgrading to `google-adk==2.5.0` (the first release allowing
    `a2a-sdk<2`) and dropping A2UI let it speak v1.0.
  - The same split later blocked `strands-agents[a2a]` on the worker side, which
    still pins `a2a-sdk<0.4`. The worker therefore builds its server from
    `a2a-sdk` directly; see `app/CurrencyWorker/main.py`.
- The v1.0 agent card moved `url`/`protocolVersion` into `supportedInterfaces`.
- **Agent cards advertise bind addresses, not reachable ones.** ADK's `to_a2a()`
  puts the server's bind address (e.g. `http://127.0.0.1:8080`) in
  `supportedInterfaces[].url`, and a2a-sdk 1.x clients route transport by card
  URL — so they fail cross-cloud unless they rewrite the card URLs to the known
  public endpoint. `coordinator/a2a_remote.py` does exactly that. (The Microsoft
  Agent Framework client ignored the card URL, which masked this.)
- Replies arrive as plain text parts, which `coordinator/a2a_remote.py` parses
  as one JSON object per target currency.

## Run locally

Needs `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) in the environment or a `.env`
file. The MCP rate server is no longer a separate HTTP service — the benchmark
tool spawns `python -m mcp_server.server` over stdio per request.

```bash
./infra/sync_adk.sh          # from the repo root
cd adk_agent && uv sync
CURRENCY_RATE_TRANSPORT=mcp-stdio uv run uvicorn agent:a2a_app \
  --host 127.0.0.1 --port 8080
```

Verify with:

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/.well-known/agent-card.json
CURRENCY_COORDINATOR_ENDPOINT=http://127.0.0.1:8080 \
  python3 -m evaluations.invoke_hosted "Convert 100 USD to EUR in mcp_only mode."
```

`a2a_only` and `verified` additionally need a reachable worker: set
`CURRENCY_A2A_ENDPOINT`. Against a deployed AgentCore runtime you also need AWS
credentials to sign with — off Cloud Run the metadata server is unavailable, so
assume the role by hand and let the signer pick up the ambient credentials. See
`docs/E2E_TESTING.md` tier 3.
