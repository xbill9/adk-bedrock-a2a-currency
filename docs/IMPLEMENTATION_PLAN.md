# Implementation plan

> **Superseded in part on 2026-07-30.** The topology was reversed: the Google
> ADK agent is now the master on Cloud Run and the AgentCore runtime is an A2A
> worker (`app/CurrencyWorker/`). Phase 4 below describes the previous
> AWS-master arrangement and is retained as a record of what was built and
> measured, not as current instructions. See `README.md` and
> `docs/ARCHITECTURE.md` for the present design.

The repository originally ran this benchmark with a Microsoft Foundry–hosted
coordinator (phases 1–5 completed and measured on 2026-07-26/27). It was then
ported to an AWS-native coordinator: Strands Agents on an Amazon Bedrock
model, hosted on Bedrock AgentCore Runtime. Phases 1–3 carry over unchanged
because the domain core is framework-independent; phase 4 is the AWS
replacement work.

## Phase 1 — deterministic local core

Status: implemented and locally verified.

- Implement input validation and result types.
- Add a fake exchange-rate provider.
- Add quote comparison with configurable tolerance.
- Expand unit tests for rounding, mismatches, stale data, and failures.
- Define the JSON result schema used by both adapters.

Exit condition: tests run without cloud credentials or model calls.

## Phase 2 — MCP baseline

Status: local stdio server and client adapter implemented; `mcp_only` (and
`verified`) run end-to-end over stdio JSON-RPC via
`currency-benchmark --transport mcp-stdio`. Malicious tool text is exercised
at the deterministic layer (carried as opaque data). Client approval flows,
model-level injection resistance, and hosted trace capture remain unobserved.

- Wrap the provider in an MCP server. — done
- Add a local MCP client adapter behind `ExchangeRateTool`. — done
  (`coordinator/mcp_stdio.py`)
- Exercise tool approval behavior and malicious tool-output cases. — partial;
  approval flows need a real MCP client host.
- Capture component latency and trace IDs. — per-quote latency observed;
  exporting trace IDs into benchmark records remains.

Exit condition: `mcp_only` passes the basic, validation, and failure cases.

## Phase 3 — Google ADK over A2A

Status: implemented and locally verified on 2026-07-27 against the original
coordinator; the adapter now uses the plain `a2a-sdk` 1.x client so it has no
coordinator-framework dependency.

- Point to the existing currency-agent repository and pin a commit. — done;
  `xbill9/currency-agent@aeef3c4`, adapted in `adk_agent/` (A2UI removed).
- Expose/verify its A2A v1.0 agent card and JSON-RPC endpoint. — done; required
  google-adk 2.5.0 (first release allowing a2a-sdk>=1.0). The upstream agent's
  A2UI dependency pins a2a-sdk<0.4 (A2A v0.3.0) and cannot answer a2a-sdk 1.x
  clients (`MethodNotFoundError`: `SendMessage` vs `message/send`). Findings
  recorded in `adk_agent/README.md`.
- Implement `RemoteCurrencyAgent` with a framework-independent A2A v1.0
  client. — done (`coordinator/a2a_remote.py`, `--a2a-endpoint` CLI flag).
- Record any schema or protocol translation required. — done; remote replies
  are prompted into one JSON object per target and parsed by pure functions
  with typed protocol/transport/timeout/authentication failures.

Exit condition met: `a2a_only` works locally and its failures are typed.

## Phase 4 — Bedrock AgentCore hosting

Status: complete. Deployed to AgentCore Runtime on 2026-07-28 (us-east-1,
stack `AgentCore-currencybench-default`) and all three benchmark modes
completed hosted the same day: verified mode returned both quotes with
primary `mcp-stdio:frankfurter-live` and verifier `gcp-adk-a2a-worker` in
agreement; a2a_only matched the MCP rate exactly (shared upstream source).
Coordinator model: Amazon Nova Micro (cheapest tool-calling Bedrock model;
Anthropic models were skipped because they require a use-case form and
marketplace agreement).

- Validate the current AgentCore CLI quickstart. — done; the pip
  starter-toolkit flow is deprecated, npm `@aws/agentcore` (CDK) works.
- Wrap the coordinator in a Strands agent with `run_currency_benchmark` as its
  only tool, served by `BedrockAgentCoreApp`. — done
  (`app/CurrencyCoordinator/main.py`; `infra/sync_app.sh` copies the domain
  packages into the bundle).
- Pin known-good AWS dependency versions after the first verified deployment.
  — pending (`requirements.txt` and the app `uv.lock` hold candidates).
- Deploy with `agentcore deploy`. — done 2026-07-28; runtime
  `currencybench_CurrencyCoordinator` active, CloudWatch logs and OTel traces
  flowing, execution-role credentials picked up automatically.
- Use the AgentCore execution role and SigV4 for invocation instead of
  embedded secrets. — done (`evaluations/invoke_hosted.py` uses the standard
  AWS credential chain).
- Point the coordinator at the public ADK A2A agent via
  `CURRENCY_A2A_ENDPOINT`, `CURRENCY_RATE_PROVIDER=frankfurter`,
  `CURRENCY_RATE_TRANSPORT=mcp-stdio`, `CURRENCY_TIMEOUT_SECONDS=60`. — done
  via `envVars` in `agentcore/agentcore.json`.
- Switch the coordinator model to Amazon Nova Micro. — done 2026-07-28;
  avoids the Anthropic use-case form/agreement entirely and minimizes cost.

Interop findings from the port (record in the article):

- ADK's `to_a2a()` advertises the server's bind address
  (`http://127.0.0.1:8080`) in the agent card's `supportedInterfaces[].url`.
  The a2a-sdk 1.x client routes transport by card URL, so cross-cloud calls
  fail with a protocol error until the client rewrites the card URLs to the
  known public endpoint (`coordinator/a2a_remote.py`). The old Microsoft
  Agent Framework client ignored the card URL, which masked this.
- Nova Micro sometimes mislabels the live `gcp-adk-a2a-worker` verifier source as
  non-live in its prose despite explicit labeling instructions; the numeric
  fields are deterministic and unaffected. A model-quality finding, not a
  protocol one.

Exit condition met 2026-07-28: the deployed AgentCore endpoint completed all
three benchmark modes against the Cloud Run ADK agent, and verified mode
agreed within tolerance.

## Phase 5 — evaluation

Status: first full live run retained on 2026-07-27 with the original
coordinator (`evaluations/results/live-2026-07-27.jsonl` + summary). 38 cases
x 3 modes; fault-free cases ran against the live ADK agent (Gemini 2.5 Flash)
and live Frankfurter rates over MCP stdio; fault cases stayed on deterministic
injection. Success rate 1.0 in all modes. Median/p95 latency: mcp_only
297/1086 ms, a2a_only 1692/4820 ms, verified 1705/4150 ms. That dataset is
coordinator-independent below the hosting layer and remains the comparison
baseline for the AWS run.

- Expand `cases.jsonl` to 30–50 cases. — done (38).
- Re-run the matrix with the a2a-sdk client stack. — done 2026-07-28: two
  full runs retained (`live-2026-07-28.jsonl` cold, `live-2026-07-28-run2.jsonl`
  warm). Run 1 had one transient: the `multi-seven` verified case lost its
  CAD quote in an incomplete multi-target reply (typed protocol failure,
  graceful degradation to unverified MCP quotes); run 2 passed 114/114 with
  success 1.0 in all modes. Warm medians: mcp_only 286 ms, a2a_only 2092 ms,
  verified 1871 ms — consistent with the 2026-07-27 baseline, so the
  Microsoft-client → a2a-sdk swap did not change protocol economics.
- Run repeated warm and cold trials. — partial (one cold + one warm trial
  retained per stack); hosted-path (AgentCore-invoked) matrix remaining.
- Export raw JSON/CSV and a reproducible summary. — done for the first run
  (`currency-evaluate --a2a-endpoint <url> --live-rates`).
- Calculate success rate, relative numeric error, tool-selection accuracy, A2A
  completion, recovery, median/p95 latency, tokens, and estimated cost. —
  success and latency done; tokens/cost and hosted-path metrics remaining.
- Manually inspect and classify failures. — remaining.

Exit condition: every published chart can be regenerated from retained raw data.

## Phase 6 — publication

- Add a final architecture diagram.
- Document exact versions, region, model, dates, and pricing assumptions.
- Write from `docs/ARTICLE_OUTLINE.md`.
- State platform limitations and distinguish observed facts from interpretation.
- Publish only measured claims backed by reproducible results.
