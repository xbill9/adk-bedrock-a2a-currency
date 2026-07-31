# ADK + Bedrock AgentCore Cross-Cloud Currency Agent

A buildable interoperability lab in which a Google ADK master, hosted on GCP
Cloud Run, calls an Amazon Bedrock AgentCore worker and verifies currency
conversions by combining:

- a live exchange-rate tool exposed through MCP;
- a remote Strands agent on Bedrock AgentCore exposed through A2A v1.0; and
- deterministic comparison and evaluation code.

The goal is not another currency chatbot. The goal is to measure what A2A adds
to a normal tool-calling workflow: accuracy, latency, cost, failure recovery,
and cross-framework portability.

**Direction matters, and this repo runs GCP → AWS.** The same domain core
previously ran the other way (AWS AgentCore master → GCP ADK worker) and,
before that, on a Microsoft Foundry–hosted coordinator. Those results are
retained in `evaluations/results/` as historical baselines. Because
`coordinator/` is framework-independent, reversing the direction changed the
hosting and the authentication story, not the comparison logic.

## Target architecture

```text
CLI / test runner
       |
Cloud Run hosted master
Google ADK on Gemini (Python)
       |
       +-- MCP (stdio) --> exchange-rate server
       |
       +-- A2A v1.0 + Google OIDC --> Bedrock AgentCore worker (Strands, Nova)
       |
       +-- OpenTelemetry traces + evaluation results
```

## Research questions

1. Can a Cloud Run–hosted ADK agent discover and invoke a Bedrock AgentCore
   agent through an A2A agent card without framework-specific glue?
2. What latency and token overhead does remote-agent verification add?
3. Does MCP plus independent A2A verification improve numeric correctness or
   failure recovery enough to justify that overhead?
4. Which failures are protocol, authentication, framework, model, or
   application failures?
5. What does keyless cross-cloud identity (GCP workload → AWS runtime) cost in
   setup complexity versus static credentials?

## MVP

The first publishable version has only:

- one Google ADK coordinator on Cloud Run;
- one Strands Agents worker on AgentCore Runtime;
- one MCP exchange-rate server;
- keyless Google OIDC authentication into AgentCore's CUSTOM_JWT authorizer;
- A2A agent-card discovery;
- structured conversion results;
- hosted trace correlation, with benchmark trace-ID export still pending; and
- 30–50 repeatable evaluation cases.

AgentCore Gateway/Memory/Identity, Amazon Q integrations, and a custom web
frontend are explicitly out of scope for the MVP.

## Repository map

```text
coordinator/       Framework-independent domain types and coordinator adapters
adk_agent/         Google ADK master agent for Cloud Run (+ synced copies)
mcp_server/        Exchange-rate MCP server adapter
app/CurrencyWorker AgentCore Runtime A2A worker (Strands + synced copies)
agentcore/         AgentCore CLI project config and CDK assets
evaluations/       Cases, runner, scorers, and generated results
infra/             Deployment scripts (Cloud Run + AgentCore) and notes
docs/              Architecture, implementation plan, and article guidance
tests/             Fast deterministic tests
```

Both deployables bundle copies of `coordinator/` (and `mcp_server/`) so their
uploads are self-contained. Those copies are build artifacts refreshed by
`infra/sync_adk.sh` and `infra/sync_app.sh` — edit the repo-root packages.

## Run locally

The repository includes a credential-free implementation with deterministic
fixture rates. Fixture results exercise the orchestration and protocols; they
are not live financial quotes.

1. Install the project and development dependencies for your user account:

   ```bash
   pip3 install --user -e ".[dev]"
   ```

2. Run the local deterministic tests:

   ```bash
   pytest
   ```

3. Exercise all three paths from the CLI:

   ```bash
   currency-benchmark 100 USD CAD EUR --mode mcp_only
   currency-benchmark 100 USD CAD EUR --mode a2a_only
   currency-benchmark 100 USD CAD EUR --mode verified --json
   ```

   Add `--transport mcp-stdio` to route the rate tool through the local MCP
   stdio server (spawned as a subprocess) instead of the in-process fixture.

4. Run the 38-case benchmark matrix (114 records). Raw JSONL goes to `--output`;
   a per-mode summary (success rate, nearest-rank median/p95 latency, agreement
   rate) prints to stderr and can be written with `--summary`:

   ```bash
   currency-evaluate --output /tmp/currency-results.jsonl --summary /tmp/currency-summary.json
   ```

5. Start the local MCP stdio server:

   ```bash
   currency-mcp-server
   ```

   It implements `initialize`, `ping`, `tools/list`, and `tools/call` for the
   `convert_currency` tool using newline-delimited JSON-RPC. Configure an MCP
   client to launch that command over stdio.

## Deploy the cross-cloud loop

Both halves deploy from one script, in dependency order — the worker must exist
before the coordinator can be pointed at it, and the coordinator's service
account must exist before the worker's authorizer can pin it:

```bash
export GCP_PROJECT=my-project
./infra/deploy_live.sh          # creates the SA, deploys the AgentCore worker
export AGENTCORE_A2A_ENDPOINT="https://...."   # from the `agentcore status` output
./infra/deploy_live.sh          # deploys the Cloud Run coordinator
```

The AWS half uses the npm `@aws/agentcore` CLI (CDK-based; the pip
starter-toolkit flow is deprecated). The worker entrypoint is
`app/CurrencyWorker/main.py`. See `infra/README.md` for one-time setup.

Authentication is keyless in both directions of setup: the coordinator mints a
Google OIDC token from the Cloud Run metadata server and AgentCore's
`CUSTOM_JWT` authorizer validates it. **Audience alone is not authorization** —
the authorizer also pins the coordinator service account's `email` claim, which
`deploy_live.sh` substitutes. See `docs/ARCHITECTURE.md`.

Coordinator configuration sets `CURRENCY_REQUIRE_AWS_AGENTCORE=1`. Therefore
`a2a_only` and `verified` fail with `agentcore_not_configured` if
`CURRENCY_A2A_ENDPOINT` is absent; they never silently substitute a local
fixture for the AgentCore worker. `mcp_only` remains usable as the baseline.

Cloud deployment remains adapter work: copy `.env.example` to `.env`, keep it
uncommitted, and validate current SDK examples before re-pinning Strands
Agents, Bedrock AgentCore, Google ADK, and A2A packages.

### A2A version constraint

`strands-agents[a2a]` pins `a2a-sdk<0.4` (A2A v0.3 wire methods) even at the
latest release, and an `a2a-sdk` 1.x client — which is what `google-adk` 2.5.0
uses — cannot call it. The worker therefore takes `strands-agents` without the
`[a2a]` extra and builds its A2A v1.0 server from `a2a-sdk[http-server]`
directly. A test asserts the lockfile keeps `a2a-sdk` on 1.x.

## Three benchmark modes

| Mode | Implementation | Purpose |
|---|---|---|
| `mcp_only` | ADK coordinator calls the rate tool | Baseline |
| `a2a_only` | ADK coordinator delegates to AgentCore | Measure remote-agent behavior |
| `verified` | MCP result checked by AgentCore over A2A | Accuracy/overhead tradeoff |

## Local implementation status

- Implemented: domain validation, `Decimal` cross-rate arithmetic, deterministic
  provider, three coordinator modes, concurrent verification, typed adapter
  failures, fallback policy, disagreement/staleness warnings, CLI, MCP stdio
  server and client adapter (subprocess JSON-RPC round trip), per-mode
  evaluation summaries, 38 evaluation cases, and deterministic tests.
- Reversed on 2026-07-30 (this revision): the ADK agent became the master
  (`adk_agent/agent.py`, Gemini) hosting the benchmark tool, and the AgentCore
  runtime became an A2A worker (`app/CurrencyWorker/main.py`, Nova Micro,
  `"protocol": "A2A"`). Cross-cloud auth moved from AWS SigV4 to keyless Google
  OIDC against a `CUSTOM_JWT` authorizer. **Not yet deployed or measured in this
  direction** — the code and config are complete and the local suite is green,
  but no live GCP → AWS run has been recorded.
- Historical baseline, AWS master → GCP worker (2026-07-28/29): all three modes
  completed hosted; verified mode agreed with `relative_difference: "0"`. See
  `infra/README.md`.
- Historical baseline, Azure-era evidence (2026-07-26/27): live Frankfurter
  rates over MCP stdio, the cross-cloud A2A v1.0 call, and a full 38-case live
  evaluation (`evaluations/results/live-2026-07-27.jsonl`).
- Not yet measured: the GCP → AWS path end to end, the Google OIDC handshake
  against a live authorizer, token usage, cloud cost, repeated warm/cold hosted
  distributions, or benchmark trace-ID export.

## Full benchmark definition of done

- A fresh user can reproduce the local tests from the README.
- A deployed coordinator calls both MCP and A2A endpoints.
- Every result records rate timestamp, source, amount, currencies, latency, and
  agreement status.
- At least 30 evaluation cases run in all three modes.
- Results include median/p95 latency, numeric accuracy, completion rate, tool
  selection accuracy, recovery rate, token use, and estimated cost.
- The article reports platform limitations and failed experiments as well as
  wins.

## Current platform references

- [Get started with AgentCore Runtime (CLI)](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-cli.html)
- [AgentCore CLI](https://github.com/aws/agentcore-cli)
- [Bedrock AgentCore Python SDK](https://github.com/aws/bedrock-agentcore-sdk-python)
- [Strands Agents quickstart](https://strandsagents.com/docs/user-guide/quickstart/python/)
- [A2A protocol support in AgentCore Runtime](https://aws.amazon.com/blogs/machine-learning/introducing-agent-to-agent-protocol-support-in-amazon-bedrock-agentcore-runtime/)
- [AgentCore Runtime inbound auth (CUSTOM_JWT)](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-inbound-auth.html)
- [agentcore.json schema](https://schema.agentcore.aws.dev/v1/agentcore.json)
- [Fetching Google OIDC ID tokens from the metadata server](https://cloud.google.com/run/docs/securing/service-identity)

These services and SDKs move quickly. Pin working versions once the first
end-to-end deployment succeeds, and record them in the article.
