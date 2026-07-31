---
title: Amazon Bedrock as the Master Agent, Calling Google ADK over A2A
published: false
series: A2A
tags: aiagent, googleadk, a2aprotocol, aws
---

This article explains how to build and test a cross-cloud currency agent. An **Amazon Bedrock master agent**, built with Strands Agents and hosted on **Amazon Bedrock AgentCore Runtime** in AWS `us-east-1`, delegates to a **Google ADK worker** on GCP Cloud Run in `us-central1` over **A2A v1.0**. The master cross-checks the worker against an MCP exchange-rate tool and measures the latency, reliability, and failure behavior of cross-cloud verification.

#### What is this project trying to do?

Most Agent-to-Agent (A2A) protocol demos stop at "look, the HTTP 200 OK request succeeded." That is a smoke test, not an interoperability benchmark.

This project goes further: the Bedrock master owns the user interaction and benchmark policy. It discovers and calls a Google ADK worker running on GCP Cloud Run, then compares the worker result with a local MCP stdio exchange-rate tool backed by live Frankfurter daily reference rates.

We also compare the performance, developer experience, and wire compatibility with a previous benchmark run using **Microsoft Foundry in Azure** (`gpt-5-mini`). Together, the runs cover components hosted across AWS, Azure, and GCP.

The benchmark addresses four questions:

1. Can an AgentCore-hosted Bedrock master discover and invoke a Google ADK worker through an A2A agent card with no framework-specific glue?
2. What latency and token overhead does remote-agent verification add?
3. Does independently verifying an MCP tool result over A2A improve correctness or failure recovery enough to justify that overhead?
4. Which measurements are portable across coordinators, and which require a fully hosted AgentCore benchmark run?

#### Reusing the original currency agent

This builds directly on the currency agent from the previous articles in this series:

* [Getting Started with MCP, ADK and A2A | Google Codelabs](https://codelabs.developers.google.com/codelabs/currency-agent#0)
* [GitHub - jackwotherspoon/currency-agent](https://github.com/jackwotherspoon/currency-agent)

That agent — built with Google ADK, Gemini 2.5 Flash, and a FastMCP exchange-rate server backed by the free [Frankfurter API](https://www.frankfurter.dev/) — serves as the remote worker and independent verifier in this project.

The new repository adds the AgentCore coordinator and benchmark suite:

* [GitHub - xbill9/bedrock-adk-a2a-currency](https://github.com/xbill9/bedrock-adk-a2a-currency)

#### Architecture

```text
CLI / Boto3 Test Runner (AWS SigV4 Auth)
       |
Bedrock AgentCore Runtime hosted master      (AWS, us-east-1, Amazon Nova Micro)
Strands Agents orchestration
       |
       +-- MCP stdio --> Frankfurter rates      (in-container stdio process)
       |
       +-- A2A v1.0 --> Cloud Run               (GCP, us-central1)
                           |
                        Google ADK worker       (gemini-2.5-flash)
                           |
                        MCP HTTP --> Frankfurter rates
```

The Bedrock master answers every conversion request through three distinct evaluation modes:

| Mode | What happens | Why it exists |
|---|---|---|
| `mcp_only` | Bedrock master calls the local MCP rate tool | Baseline single-agent performance |
| `a2a_only` | Bedrock master delegates to the GCP ADK worker over A2A v1.0 | Measure remote-agent behavior and network latency |
| `verified` | MCP result independently checked against the remote ADK agent over A2A | Measure the accuracy-versus-overhead tradeoff |

Both sides read the same Frankfurter daily reference rates on purpose: when the two clouds disagree, that measures *protocol, model, and orchestration* behavior, not data-source skew.

#### Rule one: the model never does math

Currency conversion is a poor job for an LLM and a good job for Python's `Decimal`. The domain layer is framework-independent and uses Pydantic models. Numeric agreement is evaluated in code using relative difference; no LLM is asked, "Do these numbers look close to you?"

```python
difference = abs(primary.converted_amount - verifier.converted_amount)
relative = difference / abs(primary.converted_amount)
agreed = relative <= tolerance  # default 0.005 (0.5%)
```

The failure policy is explicit rather than emergent:

* **MCP fails, A2A succeeds** → return the remote result, labeled `unverified`.
* **A2A fails, MCP succeeds** → return the tool result with a `"verification unavailable"` warning.
* **Both succeed but disagree** → return **both** quotes and issue a warning; never silently pick the LLM's preferred rate.
* **Both fail** → return a strongly typed failure (`validation`, `provider`, `authentication`, `transport`, `timeout`, `protocol`); never fabricate a rate.

Because "which layer broke" is a core research question, every adapter exception is normalized into exactly one typed failure at the boundary.

#### The wire mismatch: A2A v0.3.0 vs. v1.0

The first attempt to connect the AgentCore coordinator to the Google ADK currency agent died immediately on invocation:

```text
a2a.utils.errors.MethodNotFoundError: Method not found
```

**Observed root cause:** a protocol-version mismatch between A2A v0.3.0 and v1.0, with **no automatic fallback negotiation** in the tested client.

* The modern A2A client (`a2a-sdk>=1.0`) calls the A2A v1.0 JSON-RPC method `SendMessage`.
* Older ADK agents (`a2a-sdk 0.3.x`) only expose the v0.3.0 method `message/send`.
* The client fetched the agent card — which explicitly declared `protocolVersion: 0.3.0` — but attempted the v1.0 method anyway.

The initial ecosystem package pins were also mutually exclusive:

| Package | `a2a-sdk` Requirement | Status |
|---|---|---|
| `strands-agents 1.50.2` | `>=1.0.0,<2` | Compatible |
| `google-adk 2.1.0 – 2.4.0` | `>=0.3.4,<0.4` | Incompatible |
| `google-adk 2.5.0` | `>=0.3.4,<2` | Compatible ✅ |
| `a2ui-agent-sdk` (through 0.4.0) | `<0.4.0` | Incompatible ❌ |

`google-adk 2.5.0` updated its dependencies to support `a2a-sdk 1.x`. However, A2UI extensions currently pin the older v0.3.0 protocol. For this benchmark, A2UI was omitted so both AWS and GCP sides could operate on **A2A v1.0 (`a2a-sdk 1.1.2`)**.

#### Hosting the Bedrock master on Amazon Bedrock AgentCore

Deploying the master to Amazon Bedrock AgentCore Runtime involved navigating several fast-moving SDK and platform details observed during our build on 2026-07-28:

##### 1. Model selection: Anthropic access requirements vs. Amazon Nova Micro

In the account used for this build, Anthropic models such as Claude 3.5 Sonnet required a one-time use-case submission (`PutUseCaseForModelAccess`) and an AWS Marketplace subscription agreement. To keep the setup automated, we configured the coordinator to use **Amazon Nova Micro** (`us.amazon.nova-micro-v1:0`). Nova Micro required no approval form in our test account, supported native tool calling in the tested workflow, and produced subsecond model responses.

##### 2. Inference profile IDs

In our deployment, using the bare model ID (`amazon.nova-micro-v1:0`) returned an HTTP 400 `ValidationException` requiring on-demand throughput configuration. Passing the regional **inference profile ID** (`us.amazon.nova-micro-v1:0`) resolved the error.

##### 3. CLI tooling transition

The older Python `pip`-based starter toolkit (`agentcore configure` / `agentcore launch`) was deprecated in June 2026. Deployment now uses the official `@aws/agentcore` npm CLI (Node 20+, CDK-based).

##### Coordinator entry point (abridged from `app/CurrencyCoordinator/main.py`)

```python
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from coordinator.hosted_tool import run_currency_benchmark
from model.load import load_model

app = BedrockAgentCoreApp()
tools = [tool(run_currency_benchmark)]

# The full source defines a bounded, session-scoped agent factory here.

@app.entrypoint
async def invoke(payload, context):
    session_id = getattr(context, "session_id", "default-session")
    agent = get_or_create_agent(session_id)
    prompt = payload.get("prompt", payload.get("messages", ""))
    result = await agent.invoke_async(prompt)
    return {"result": str(result)}

if __name__ == "__main__":
    app.run()
```

The hosted runtime also fails closed when its GCP worker is missing:

```json
{ "name": "CURRENCY_REQUIRE_GCP_ADK", "value": "1" }
```

With that setting, `a2a_only` and `verified` return
`gcp_adk_not_configured` if `CURRENCY_A2A_ENDPOINT` is absent. A deployment
can no longer appear to exercise A2A while silently using a local fixture.
The Bedrock model configuration also sets `BEDROCK_MAX_TOKENS=1024`
explicitly to bound output and quota usage.

#### The Google side: ADK on Cloud Run

The remote verifier container colocates two processes: the FastMCP Frankfurter server on localhost and the A2A app listening on `$PORT`. Gemini API keys are retrieved securely from GCP Secret Manager:

```bash
gcloud secrets create gemini-api-key --data-file="$HOME/gemini.key"
gcloud run deploy currency-adk-a2a \
  --source adk_agent --region us-central1 \
  --allow-unauthenticated --min-instances=0 --max-instances=2 \
  --set-secrets "GOOGLE_API_KEY=gemini-api-key:latest" \
  --set-env-vars "MCP_SERVER_URL=http://127.0.0.1:8081/mcp,GENAI_MODEL=gemini-2.5-flash"
```

Setting `--min-instances=0` allows Cloud Run to scale to zero when idle. The coordinator's timeout is set to 60 seconds to accommodate initial container cold starts.

#### How to run the benchmark

The repository includes a complete local test suite that runs deterministically without credentials or cloud infrastructure:

```bash
# 1. Clone & install dependencies
git clone https://github.com/xbill9/bedrock-adk-a2a-currency
cd bedrock-adk-a2a-currency
pip3 install --user -e ".[dev]"

# 2. Run unit and integration tests (deterministic fixtures)
pytest

# 3. Test local CLI modes
currency-benchmark 100 USD CAD EUR --mode mcp_only
currency-benchmark 100 USD CAD EUR --mode verified --transport mcp-stdio

# 4. Execute full evaluation matrix
currency-evaluate --output /tmp/currency-results.jsonl --summary /tmp/currency-summary.json
```

To deploy and test the hosted Bedrock master:

```bash
./infra/sync_app.sh
agentcore deploy -y
agentcore invoke "Convert 100 USD to EUR and CHF in verified mode."
```

#### Hosted smoke test: Bedrock master → GCP ADK worker

On 2026-07-29, I deployed the updated master to AgentCore Runtime in
`us-east-1` and invoked all three modes through the hosted
`InvokeAgentRuntime` API:

| Hosted mode | Observed result |
|---|---|
| `mcp_only` | HTTP 200; live `mcp-stdio:frankfurter-live` quote |
| `a2a_only` | HTTP 200; live `gcp-adk-a2a-worker` quote |
| `verified` | HTTP 200; MCP and GCP ADK agreed exactly for EUR and CHF |

The verified request converted 100 USD to EUR and CHF. The deterministic
comparison recorded `relative_difference: "0"` and `agreed: true` for both
currencies, with no failures or warnings. The benchmark tool completed in
approximately 3.08 seconds.

This was an end-to-end smoke test, not a full hosted latency distribution. It
exercised the complete path:

```text
AWS SigV4 invocation
  → AgentCore Runtime
  → Nova Micro tool selection
  → MCP stdio / Frankfurter
  → A2A v1.0
  → GCP Cloud Run
  → Google ADK / Gemini
  → deterministic Decimal comparison
```

The smoke test also found a real orchestration bug. On the first request,
Nova Micro read “Convert 100 USD to EUR” but claimed the target currency was
missing and asked the user to confirm it. The master prompt now includes an
explicit natural-language parsing rule and forbids confirmation requests for
information already present. After redeployment, the same request called the
benchmark tool directly. A regression test preserves that behavior.

#### Cross-cloud benchmark results

We executed the 38-case evaluation matrix across all three modes: 114 records
per run. The 2026-07-28 warm run exercised the framework-independent
coordinator locally against the live GCP Cloud Run ADK endpoint; it did
**not** measure the AgentCore hosting layer. The 2026-07-27 run is the
retained Azure-era baseline. Keeping those labels explicit avoids attributing
local harness latency to AgentCore.

| Observed run | Evaluation mode | Success rate | Median latency | p95 latency | Agreement rate |
|---|---|---|---|---|---|
| 2026-07-28 warm local harness → GCP | `mcp_only` | 100% | 286 ms | 540 ms | N/A |
| 2026-07-28 warm local harness → GCP | `a2a_only` | 100% | 2.09 s | 6.10 s | N/A |
| 2026-07-28 warm local harness → GCP | `verified` | 100% | 1.87 s | 4.33 s | 96.77% |
| 2026-07-27 Azure-era baseline → GCP | `mcp_only` | 100% | 297 ms | 1.09 s | N/A |
| 2026-07-27 Azure-era baseline → GCP | `a2a_only` | 100% | 1.69 s | 4.82 s | N/A |
| 2026-07-27 Azure-era baseline → GCP | `verified` | 100% | 1.71 s | 4.15 s | 96.77% |

##### Key findings

1. **The live protocol path was reliable:** the warm 2026-07-28 run completed all 114 records successfully. Fault-injection cases are included in the aggregate, so agreement rate is not expected to be 100%.
2. **Concurrent execution limits verification overhead:** verified-mode latency is dominated by the remote A2A round trip rather than the sum of MCP and A2A latency.
3. **Hosted AWS → GCP interoperability was observed:** all three modes completed through AgentCore. The verified EUR and CHF quotes had zero relative difference, no failures, and no warnings. This remains a smoke-test result, not a 114-record hosted latency distribution.
4. **Hosted performance remains to be measured:** token usage, cost, and repeated warm/cold AgentCore distributions are still open benchmark work.

#### Lessons learned

1. **Check A2A SDK major versions first:** A2A v0.3.0 (`message/send`) and v1.0 (`SendMessage`) are wire-incompatible. If you see `MethodNotFoundError`, inspect the `a2a-sdk` version on both client and server before debugging prompt logic.
2. **Use inference profile IDs on Bedrock:** In our hosted deployment, the regional inference profile ID (`us.amazon.nova-micro-v1:0`) avoided the on-demand throughput error returned for the bare model ID.
3. **Account for remote cold starts:** A 10-second client timeout worked locally, but the Cloud Run scale-from-zero path needed a longer window. We used 60 seconds for this benchmark.
4. **Keep math out of the prompt:** Deterministic Python `Decimal` arithmetic prevents LLM calculation errors from affecting conversion and agreement checks. The checks therefore measure differences in returned results, not the model's arithmetic ability.
5. **A2A verification provides independent fault detection:** The faster `mcp_only` path is useful as a baseline, while cross-cloud A2A verification adds an independent result for failover and anomaly detection. Whether the overhead is justified depends on the workload.
6. **Test natural-language argument extraction:** Tool availability is not enough. The master model can still fail before invocation by misreading an argument that is plainly present. Keep a hosted smoke case for natural-language parsing, not only structured tool calls.

#### Repository and source code

The complete benchmark codebase, deployment scripts, test suite, and raw evaluation datasets are available on GitHub:

* [GitHub - xbill9/bedrock-adk-a2a-currency](https://github.com/xbill9/bedrock-adk-a2a-currency)

If you are building multi-cloud agent systems with Amazon Bedrock AgentCore, Google ADK, or Microsoft Agent Framework, feedback and benchmark contributions are welcome.
