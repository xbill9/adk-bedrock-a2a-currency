# End-to-end testing

The benchmark runs at five tiers. Each tier adds one real dependency, so a
failure always points at the layer that was just introduced. Run them in
order when validating a change; run only tier 4 to smoke-test the deployed
system.

| Tier | What runs | Needs |
|---|---|---|
| 0 | Deterministic tests, fixture adapters | nothing |
| 1 | Live ADK agent + live rates, local | Gemini key |
| 2 | Full 38-case matrix vs live local stack | Gemini key |
| 3 | Local coordinator → Cloud Run agent | deployed `currency-adk-a2a` |
| 4 | AgentCore-hosted coordinator → Cloud Run agent | full deployment + AWS credentials |

## Prerequisites

- Python 3.13, `pip3 install --user -e ".[dev]"` from the repo root, and
  `pip3 install --user -r requirements.txt` for tiers 3–4.
- `uv` (for the ADK agent's own venv).
- A Gemini API key exported as `GOOGLE_API_KEY` (tiers 1–2). Keep
  `GOOGLE_GENAI_USE_VERTEXAI` unset.
- AWS credentials with `bedrock-agentcore:InvokeAgentRuntime` on the deployed
  runtime, e.g. via `aws configure` or SSO (tier 4).

## Tier 0 — deterministic core (no credentials)

```bash
pytest
currency-benchmark 100 USD EUR --mode verified --transport mcp-stdio
```

Expect 46 tests passing and a fixture-labeled quote
(`mcp-stdio:deterministic-fixture`). Anything failing here is a code
regression, not an integration problem.

## Tier 1 — live local stack

Start the benchmark ADK agent (serves A2A v1.0 on port 10001; its MCP rate
server URL defaults to port 8081):

```bash
cd adk_agent && uv sync
uv run python mcp_server.py &                 # Frankfurter MCP on :8081
MCP_SERVER_URL=http://127.0.0.1:8081/mcp \
  uv run uvicorn agent:a2a_app --host 127.0.0.1 --port 10001 &
curl -s http://127.0.0.1:10001/health          # {"status":"ok"}
```

Then, from the repo root, run all three modes against it:

```bash
CURRENCY_RATE_PROVIDER=frankfurter currency-benchmark 250 GBP USD JPY \
  --mode verified --transport mcp-stdio \
  --a2a-endpoint http://127.0.0.1:10001 --timeout-seconds 60
```

Expect quotes labeled `mcp-stdio:frankfurter-live` with ` verified` suffixes.
A missing suffix plus a warning means one side failed — the failure kind in
the output says which.

## Tier 2 — full evaluation matrix

```bash
CURRENCY_RATE_PROVIDER=frankfurter currency-evaluate \
  --a2a-endpoint http://127.0.0.1:10001 --live-rates \
  --output evaluations/results/live-$(date +%F).jsonl \
  --summary evaluations/results/summary-live-$(date +%F).json
```

38 cases x 3 modes = 114 records; fault-free cases use the live adapters,
fault-injection cases stay deterministic (a live agent cannot be ordered to
time out). Takes a few minutes; exit code 0 means every case met its expected
behavior. Compare against the retained baselines
`evaluations/results/live-2026-07-27.jsonl` (Microsoft-client stack) and
`live-2026-07-28-run2.jsonl` (a2a-sdk stack; success 1.0 in all modes; the
only `agreed: false` should be case `a2a-disagreement`).

## Tier 3 — cross-cloud from the local coordinator

Requires the Cloud Run deployment (`infra/deploy_live.sh` steps 1–2, or see
`adk_agent/README.md`). The deployment script requires `GCP_PROJECT`; it never
stores a project ID or local key path in Git.

```bash
A2A_URL=$(gcloud run services describe currency-adk-a2a \
  --region us-central1 --format='value(status.url)')
curl -s "$A2A_URL/health"
CURRENCY_RATE_PROVIDER=frankfurter currency-benchmark 250 GBP USD JPY \
  --mode verified --transport mcp-stdio \
  --a2a-endpoint "$A2A_URL" --timeout-seconds 60
```

Keep `--timeout-seconds 60`: a Cloud Run cold start plus multi-target
generation exceeds the 10 s default, and cold starts can also produce
partial replies (typed as `protocol` failures — rerun once warm before
concluding anything is broken).

## Tier 4 — fully hosted (AWS -> GCP)

Requires the full deployment (`infra/deploy_live.sh`) and AWS credentials
with `bedrock-agentcore:InvokeAgentRuntime` on the runtime ARN (see
`infra/README.md`).

```bash
export AGENTCORE_RUNTIME_ARN="arn:aws:bedrock-agentcore:REGION:ACCOUNT:runtime/NAME"
python3 -m evaluations.invoke_hosted "Convert 500 EUR to USD and CHF in verified mode."
python3 -m evaluations.invoke_hosted "Convert 500 EUR to USD in mcp_only mode."
python3 -m evaluations.invoke_hosted "Convert 500 EUR to USD in a2a_only mode."
```

Expect the verified run to include `"agreed": true` with sources
`mcp-stdio:frankfurter-live` (rates fetched inside the AgentCore container)
and `gcp-adk-a2a-worker` (the Cloud Run agent). This exercises every hop: SigV4
auth, the AgentCore invocation contract, Nova Micro tool selection on
Bedrock, MCP stdio, and the cross-cloud A2A v1.0 call to Gemini. The
equivalent Azure-hosted tier passed on 2026-07-27; the AWS leg passed on
2026-07-28 (all three modes, verified mode in agreement).

## Known transient failures
- Empty-message provider timeouts locally — IPv6 hang; the provider pins
  IPv4, but other HTTP paths in new code may need the same treatment.
- First request after Cloud Run scale-to-zero is slow (~10 s) and
  occasionally incomplete; warm requests are the meaningful signal.
