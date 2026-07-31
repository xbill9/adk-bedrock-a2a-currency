# Bedrock AgentCore + ADK Cross-Cloud Currency Agent

A buildable interoperability lab in which a Strands Agents master, hosted on
Amazon Bedrock AgentCore Runtime, calls a GCP Google ADK worker and verifies
currency conversions by
combining:

- a live exchange-rate tool exposed through MCP;
- a remote Google ADK currency agent exposed through A2A v1.0; and
- deterministic comparison and evaluation code.

The goal is not another currency chatbot. The goal is to measure what A2A adds
to a normal tool-calling workflow: accuracy, latency, cost, failure recovery,
and cross-framework portability. The same domain core previously ran with a
Microsoft Foundry–hosted coordinator (results retained in
`evaluations/results/`), so the AWS coordinator can be compared like-for-like.

## Target architecture

```text
CLI / test runner
       |
Bedrock AgentCore Runtime hosted master
Strands Agents on a Bedrock model (Python)
       |
       +-- MCP --> exchange-rate server
       |
       +-- A2A v1.0 --> GCP Google ADK worker (Cloud Run, Gemini)
       |
       +-- OpenTelemetry traces + evaluation results
```

## Research questions

1. Can an AgentCore-hosted Strands agent discover and invoke a Google ADK
   agent through an A2A agent card without framework-specific glue?
2. What latency and token overhead does remote-agent verification add?
3. Does MCP plus independent A2A verification improve numeric correctness or
   failure recovery enough to justify that overhead?
4. Which failures are protocol, authentication, framework, model, or
   application failures?

## MVP

The first publishable version has only:

- one Python Strands Agents coordinator;
- one existing Google ADK currency agent;
- one MCP exchange-rate server;
- one AgentCore Runtime deployment;
- AWS IAM/SigV4 authentication via the standard credential chain;
- A2A agent-card discovery;
- structured conversion results;
- hosted trace correlation, with benchmark trace-ID export still pending; and
- 30–50 repeatable evaluation cases.

AgentCore Gateway/Memory/Identity, Amazon Q integrations, and a custom web
frontend are explicitly out of scope for the MVP.

## Repository map

```text
coordinator/       Framework-independent domain types and coordinator adapters
adk_agent/         Runnable Google ADK agent exposed over A2A v1.0
mcp_server/        Exchange-rate MCP server adapter
app/               AgentCore Runtime app (Strands entrypoint + synced copies)
agentcore/         AgentCore CLI project config and CDK assets
evaluations/       Cases, runner, scorers, and generated results
infra/             Deployment scripts (Cloud Run + AgentCore) and notes
docs/              Architecture, implementation plan, and article guidance
tests/             Fast deterministic tests
```

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

## Deploy the hosted coordinator

The AgentCore deployment uses the npm `@aws/agentcore` CLI (CDK-based; the
pip starter-toolkit flow is deprecated). The hosted entrypoint is
`app/CurrencyCoordinator/main.py`; `infra/sync_app.sh` copies `coordinator/`
and `mcp_server/` into the app bundle before each deploy. See
`infra/README.md` for the one-time setup, then:

```bash
./infra/sync_app.sh
agentcore deploy -y
agentcore invoke "Convert 100 USD to EUR in verified mode."
```

Direct hosted-agent dependencies are pinned in `requirements.txt`. Update those
pins only after a new end-to-end deployment has been verified, and retain the
working versions with the benchmark evidence.

Hosted configuration sets `CURRENCY_REQUIRE_GCP_ADK=1`. Therefore `a2a_only`
and `verified` fail with `gcp_adk_not_configured` if
`CURRENCY_A2A_ENDPOINT` is absent; they never silently substitute a local
fixture for the GCP ADK worker. `mcp_only` remains usable as the baseline.

Cloud deployment remains adapter work: copy `.env.example` to `.env`, keep it
uncommitted, and validate current SDK examples before re-pinning Strands
Agents, Bedrock AgentCore, Google ADK, and A2A packages.

## Three benchmark modes

| Mode | Implementation | Purpose |
|---|---|---|
| `mcp_only` | AgentCore coordinator calls the rate tool | Baseline |
| `a2a_only` | AgentCore coordinator delegates to ADK | Measure remote-agent behavior |
| `verified` | MCP result checked by ADK over A2A | Accuracy/overhead tradeoff |

## Local implementation status

- Implemented: domain validation, `Decimal` cross-rate arithmetic, deterministic
  provider, three coordinator modes, concurrent verification, typed adapter
  failures, fallback policy, disagreement/staleness warnings, CLI, MCP stdio
  server and client adapter (subprocess JSON-RPC round trip), per-mode
  evaluation summaries, 38 evaluation cases, and deterministic tests.
- Ported and deployed on 2026-07-28: coordinator hosting moved from Microsoft
  Foundry to Strands + AgentCore (`app/CurrencyCoordinator/main.py`, Amazon
  Nova Micro), the A2A adapter moved to the plain `a2a-sdk` 1.x client, and
  hosted invocation moved to `bedrock-agentcore:InvokeAgentRuntime`. All
  three modes completed hosted the same day (AWS us-east-1 → GCP Cloud Run);
  verified mode agreed. See `infra/README.md` for the observed results.
- Retained Azure-era evidence (2026-07-26/27): hosted provisioning, live
  Frankfurter rates over MCP stdio, the cross-cloud A2A v1.0 call to the
  Cloud Run ADK agent, and a full 38-case live evaluation
  (`evaluations/results/live-2026-07-27.jsonl`). Those measurements are the
  baseline for the AWS run; see `infra/README.md`.
- Not yet measured: the AgentCore-hosted path end to end, token usage, cloud
  cost, repeated warm/cold hosted distributions, or benchmark trace-ID export.

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

These services and SDKs move quickly. Pin working versions once the first
end-to-end deployment succeeds, and record them in the article.
