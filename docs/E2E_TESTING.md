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

Requires the AgentCore worker deployment (`infra/deploy_live.sh` through step
2). The local CLI stands in for the Cloud Run master and calls the AWS worker
directly.

Because the metadata server is unavailable off Cloud Run, mint a token by hand.
The audience must match `allowedAudience` in `agentcore/agentcore.json`, and the
authorizer pins the *coordinator service account's* email — so impersonate that
account rather than using your own user identity:

```bash
export CURRENCY_A2A_BEARER_TOKEN=$(gcloud auth print-identity-token \
  --impersonate-service-account="currency-coordinator@${GCP_PROJECT}.iam.gserviceaccount.com" \
  --audiences=currencybench-agentcore-worker)

CURRENCY_RATE_PROVIDER=frankfurter currency-benchmark 250 GBP USD JPY \
  --mode verified --transport mcp-stdio \
  --a2a-endpoint "$AGENTCORE_A2A_ENDPOINT" --timeout-seconds 60
```

`CURRENCY_A2A_BEARER_TOKEN` bypasses the metadata server entirely; without it
the run fails with an `authentication` error rather than falling back to a
fixture.

Keep `--timeout-seconds 60`: a cold start plus multi-target generation exceeds
the 10 s default, and cold starts can also produce partial replies (typed as
`protocol` failures — rerun once warm before concluding anything is broken).

## Tier 4 — fully hosted (GCP -> AWS)

Requires the full deployment (`infra/deploy_live.sh`). No AWS credentials are
needed on the caller: the benchmark drives the Cloud Run coordinator, which
authenticates to AgentCore with a Google OIDC token of its own (see
`infra/README.md`).

```bash
export CURRENCY_COORDINATOR_ENDPOINT="https://currency-adk-coordinator-....run.app"
python3 -m evaluations.invoke_hosted "Convert 500 EUR to USD and CHF in verified mode."
python3 -m evaluations.invoke_hosted "Convert 500 EUR to USD in mcp_only mode."
python3 -m evaluations.invoke_hosted "Convert 500 EUR to USD in a2a_only mode."
```

If the Cloud Run service was deployed without `--allow-unauthenticated`, also
set `CURRENCY_COORDINATOR_TOKEN=$(gcloud auth print-identity-token)`.

Expect the verified run to include `"agreed": true` with sources
`mcp-stdio:frankfurter-live` (rates fetched inside the Cloud Run container)
and `aws-agentcore-a2a-worker` (the Bedrock agent). This exercises every hop:
the Google OIDC mint, AgentCore's CUSTOM_JWT authorizer, Gemini tool selection,
MCP stdio, and the cross-cloud A2A v1.0 call to Nova Micro.

**Not yet run in this direction.** The equivalent Azure-hosted tier passed on
2026-07-27 and the AWS-master leg on 2026-07-28; the reversed loop has not been
exercised live. Treat a first failure here as an unknown, not a regression —
`infra/README.md` lists the specific unverified assumptions.

Authentication failures are reported as `authentication`, distinct from
`protocol`, so a rejected token is not mistaken for a wire-format problem. A 403
from the authorizer most likely means the `email` claim was never substituted
into `agentcore/agentcore.json`, or the audience does not match.

## Known transient failures
- Empty-message provider timeouts locally — IPv6 hang; the provider pins
  IPv4, but other HTTP paths in new code may need the same treatment.
- First request after Cloud Run scale-to-zero is slow (~10 s) and
  occasionally incomplete; warm requests are the meaningful signal.
