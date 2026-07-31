---
title: Can Google ADK Talk to Amazon Bedrock AgentCore Runtime? A Cross-Cloud A2A Benchmark
published: true
series: A2A
tags: aiagent, googleadk, a2aprotocol, aws
canonical_url: https://dev.to/aws-builders/cross-cloud-a2a-agent-benchmarking-4ab7
---

This article provides a step-by-step guide to building and testing a cross-cloud currency agent. A coordinator built with **Strands Agents** and hosted on **Amazon Bedrock AgentCore Runtime** (in AWS `us-east-1`) discovers and delegates to a **Google ADK agent** (on GCP Cloud Run in `us-central1`) over **A2A v1.0**, cross-checks results against an **MCP exchange-rate tool**, and measures what independent cross-cloud verification costs in latency, reliability, and overhead.

#### What is This Project Trying to Do?

Most Agent-to-Agent (A2A) protocol demos stop at "look, the HTTP 200 OK request succeeded." That is a smoke test, not an interoperability benchmark. 

This project goes further: an Amazon Bedrock AgentCore-hosted Strands Agents coordinator discovers and delegates to a Google ADK agent running on GCP Cloud Run, comparing the results against a local MCP stdio exchange-rate tool backed by live Frankfurter daily reference rates.

We also compare the performance, developer experience, and wire compatibility directly against our previous benchmark run hosted on **Microsoft Foundry in Azure** (`gpt-5-mini`), giving us a true cross-cloud benchmark across AWS, Azure, and GCP.

The questions we answer with hard empirical data rather than vibes:

1. Can an AgentCore-hosted Strands agent discover and invoke a Google ADK agent through an A2A agent card with no framework-specific glue?
2. What latency and token overhead does remote-agent verification add?
3. Does independently verifying an MCP tool result over A2A improve correctness or failure recovery enough to justify that overhead?
4. How does AWS Bedrock AgentCore Runtime compare like-for-like with Microsoft Foundry on Azure?

#### Reduce, Re-Use, Re-Cycle!

This builds directly on the currency agent from the previous articles in this series:

* [Getting Started with MCP, ADK and A2A | Google Codelabs](https://codelabs.developers.google.com/codelabs/currency-agent#0)
* [GitHub - jackwotherspoon/currency-agent](https://github.com/jackwotherspoon/currency-agent)

That agent — built with Google ADK, Gemini 2.5 Flash, and a FastMCP exchange-rate server backed by the free [Frankfurter API](https://www.frankfurter.dev/) — serves as the *remote verifier* in this project. 

The new repository wraps the AgentCore coordinator and benchmark suite:

* [GitHub - xbill9/bedrock-adk-a2a-currency](https://github.com/xbill9/bedrock-adk-a2a-currency)

#### The Architecture

```text
CLI / Boto3 Test Runner (AWS SigV4 Auth)
       |
Bedrock AgentCore Runtime hosted agent       (AWS, us-east-1, Amazon Nova Micro)
Strands Agents coordinator
       |
       +-- MCP stdio --> Frankfurter rates      (in-container stdio process)
       |
       +-- A2A v1.0 --> Cloud Run               (GCP, us-central1)
                           |
                        Google ADK agent        (gemini-2.5-flash)
                           |
                        MCP HTTP --> Frankfurter rates
```

The coordinator answers every conversion request in three distinct evaluation modes:

| Mode | What happens | Why it exists |
|---|---|---|
| `mcp_only` | Coordinator calls the local MCP rate tool | Baseline single-agent performance |
| `a2a_only` | Coordinator delegates to the remote ADK agent over A2A v1.0 | Measure remote-agent behavior and network latency |
| `verified` | MCP result independently checked against remote ADK agent over A2A | Measuring the accuracy vs. overhead tradeoff |

Both sides read the same Frankfurter daily reference rates on purpose: when the two clouds disagree, that measures *protocol, model, and orchestration* behavior, not data-source skew.

#### Rule One: The Model Never Does Math

Currency conversion is a terrible job for an LLM and a great job for Python's `Decimal`. The domain layer is completely framework-independent with Pydantic models. Numeric agreement is evaluated strictly in code via relative difference — no LLM is ever asked "do these numbers look close to you?":

```python
difference = abs(primary.converted_amount - verifier.converted_amount)
relative = difference / abs(primary.converted_amount)
agreed = relative <= tolerance  # default 0.005 (0.5%)
```

The failure policy is explicit rather than emergent:

* **MCP fails, A2A succeeds** → return the remote result, labeled `unverified`.
* **A2A fails, MCP succeeds** → return the tool result with a `"verification unavailable"` warning.
* **Both succeed but disagree** → return **both** quotes and issue a warning. Never silently pick the LLM's preferred rate.
* **Both fail** → return a strongly-typed failure (`validation`, `provider`, `authentication`, `transport`, `timeout`, `protocol`). Never fabricate a rate.

Because "which layer broke" is a core research question, every adapter exception is normalized into exactly one typed failure at the boundary.

#### The Wire Fight: A2A v0.3.0 vs v1.0 & AWS Interop Lessons

The first attempt to connect the AgentCore coordinator to the Google ADK currency agent died immediately on invocation:

```text
a2a.utils.errors.MethodNotFoundError: Method not found
```

**Root cause:** A protocol-version mismatch between A2A v0.3.0 and v1.0 with **no automatic fallback negotiation**.

* The modern A2A client (`a2a-sdk>=1.0`) calls the A2A v1.0 JSON-RPC method `SendMessage`.
* Older ADK agents (`a2a-sdk 0.3.x`) only expose the v0.3.0 method `message/send`.
* The client fetched the agent card — which explicitly declared `protocolVersion: 0.3.0` — but attempted the v1.0 method anyway.

Furthermore, ecosystem package pins were initially mutually exclusive:

| Package | `a2a-sdk` Requirement | Status |
|---|---|---|
| `strands-agents 1.50.2` | `>=1.0.0,<2` | Compatible |
| `google-adk 2.1.0 – 2.4.0` | `>=0.3.4,<0.4` | Incompatible |
| `google-adk 2.5.0` | `>=0.3.4,<2` | Compatible ✅ |
| `a2ui-agent-sdk` (through 0.4.0) | `<0.4.0` | Incompatible ❌ |

`google-adk 2.5.0` updated its dependencies to support `a2a-sdk 1.x`. However, A2UI extensions currently pin the older v0.3.0 protocol. For this benchmark, A2UI was omitted so both AWS and GCP sides could operate on **A2A v1.0 (`a2a-sdk 1.1.2`)**.

#### Hosting the Coordinator on Amazon Bedrock AgentCore

Deploying the coordinator to Amazon Bedrock AgentCore Runtime involved navigating several fast-moving SDK and platform details observed during our build on 2026-07-28:

##### 1. Model Selection: Anthropic Marketplace Forms vs. Amazon Nova Micro
Anthropic models (such as Claude 3.5 Sonnet) on Amazon Bedrock now require a one-time use-case submission (`PutUseCaseForModelAccess`) and an AWS Marketplace subscription agreement. To eliminate deployment friction and keep setup fully automated, we configured the coordinator to use **Amazon Nova Micro** (`us.amazon.nova-micro-v1:0`). Nova Micro required zero approval forms, supported native tool-calling flawlessly, and delivered sub-second model responses.

##### 2. Inference Profile IDs are Mandatory
On newer Bedrock model releases, using bare model IDs (e.g. `amazon.nova-micro-v1:0`) throws an HTTP 400 `ValidationException` requiring on-demand throughput configuration. Passing the regional **Inference Profile ID** (`us.amazon.nova-micro-v1:0`) resolved this requirement immediately.

##### 3. CLI Tooling Transition
The older Python `pip`-based starter toolkit (`agentcore configure` / `agentcore launch`) was deprecated in June 2026. Deployment now uses the official `@aws/agentcore` npm CLI (Node 20+, CDK-based).

##### Coordinator Entrypoint (`app/CurrencyCoordinator/main.py`):

```python
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from coordinator.hosted_tool import run_currency_benchmark
from model.load import load_model

app = BedrockAgentCoreApp()
tools = [tool(run_currency_benchmark)]

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

#### The Google Side: ADK on Cloud Run

The remote verifier container colocates two processes: the FastMCP Frankfurter server on localhost and the A2A app listening on `$PORT`. Gemini API keys are retrieved securely from GCP Secret Manager:

```bash
gcloud secrets create gemini-api-key --data-file="$HOME/gemini.key"
gcloud run deploy currency-adk-a2a \
  --source adk_agent --region us-central1 \
  --allow-unauthenticated --min-instances=0 --max-instances=2 \
  --set-secrets "GOOGLE_API_KEY=gemini-api-key:latest" \
  --set-env-vars "MCP_SERVER_URL=http://127.0.0.1:8081/mcp,GENAI_MODEL=gemini-2.5-flash"
```

Setting `--min-instances=0` ensures zero infrastructure costs when idle, while the coordinator's timeout is set to 60 seconds to gracefully handle initial Cloud Run container cold starts.

#### How to Run the Benchmark

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

To deploy and test the hosted AgentCore coordinator:

```bash
./infra/sync_app.sh
agentcore deploy -y
agentcore invoke "Convert 100 USD to EUR in verified mode."
```

#### Cross-Cloud Benchmark Results: AWS Bedrock vs. Azure Foundry

We executed the 38-case evaluation matrix across all three modes (114 evaluation runs per coordinator cloud). Here is how Amazon Bedrock AgentCore Runtime compares with Microsoft Foundry on Azure running the exact same benchmark harness:

| Coordinator Platform | Coordinator Model | Verifier Agent (GCP) | Evaluation Mode | Success Rate | Median Latency | p95 Latency | Numeric Agreement Rate |
|---|---|---|---|---|---|---|---|
| **Amazon Bedrock AgentCore** | Amazon Nova Micro | Google ADK (Gemini 2.5) | `mcp_only` | 100% | 312 ms | 1.12 s | N/A |
| **Amazon Bedrock AgentCore** | Amazon Nova Micro | Google ADK (Gemini 2.5) | `a2a_only` | 100% | 1.74 s | 4.89 s | N/A |
| **Amazon Bedrock AgentCore** | Amazon Nova Micro | Google ADK (Gemini 2.5) | `verified` | 100% | 1.76 s | 4.21 s | **100% (0.0% diff)** |
| **Microsoft Foundry (Azure)** | GPT-5 mini | Google ADK (Gemini 2.5) | `mcp_only` | 100% | 297 ms | 1.09 s | N/A |
| **Microsoft Foundry (Azure)** | Microsoft Agent Framework | Google ADK (Gemini 2.5) | `a2a_only` | 100% | 1.69 s | 4.82 s | N/A |
| **Microsoft Foundry (Azure)** | GPT-5 mini | Google ADK (Gemini 2.5) | `verified` | 100% | 1.71 s | 4.15 s | **100% (0.0% diff)** |

##### Key Insights from the Benchmark:

1. **Perfect Cross-Cloud Agreement:** All live conversion records across both AWS → GCP and Azure → GCP paths agreed within 0.0% relative difference (both sides using Frankfurter reference rates).
2. **Concurrent Execution Minimizes Verification Overhead:** Because the coordinator executes the local MCP tool call and the remote A2A verification request concurrently, `verified` mode latency (~1.76 s) is dominated by the remote A2A network round-trip, rather than paying the cumulative sum of both paths (~2.05 s).
3. **Nova Micro Efficiency:** Amazon Nova Micro on Bedrock AgentCore matched `gpt-5-mini` on Azure Foundry in tool selection accuracy (100% success rate) while operating with lower per-token inference costs and requiring zero prerequisite marketplace onboarding forms.
4. **Hosted Invocation Performance:** End-to-end boto3 SigV4 invocation of the hosted AgentCore runtime (`AWS us-east-1` → `GCP Cloud Run us-central1`) completed in ~12 seconds wall-clock time including Cloud Run container warm-up, with per-quote execution averaging ~3.9 s.

#### Lessons Learned

1. **Check A2A SDK Major Versions First:** A2A v0.3.0 (`message/send`) and v1.0 (`SendMessage`) are wire-incompatible. If you see `MethodNotFoundError`, inspect the `a2a-sdk` version on both client and server before debugging prompt logic.
2. **Use Inference Profile IDs on Bedrock:** Newer Bedrock models require regional inference profile IDs (e.g. `us.amazon.nova-micro-v1:0`) to avoid on-demand throughput errors during hosted execution.
3. **Account for Remote Cold Starts:** Default 10-second client timeouts are sufficient locally, but remote cross-cloud calls (e.g., Cloud Run scale-from-zero) require a minimum 60-second timeout window.
4. **Keep Math Out of the Prompt:** Using deterministic Python `Decimal` arithmetic for conversion logic eliminates LLM calculation errors entirely, ensuring agreement checks evaluate protocol and model transportation integrity rather than arithmetic capabilities.
5. **A2A Verification Provides Independent Fault Detection:** While `mcp_only` (312 ms) is ideal for simple user queries, cross-cloud A2A verification adds independent failover and anomaly detection against compromised or stale tool endpoints for mission-critical operations.

#### Repository & Source Code

The complete benchmark codebase, deployment scripts, test suite, and raw evaluation datasets are available on GitHub:

* [GitHub - xbill9/bedrock-adk-a2a-currency](https://github.com/xbill9/bedrock-adk-a2a-currency)

If you are building multi-cloud agent systems using Amazon Bedrock AgentCore, Google ADK, or Microsoft Agent Framework, we welcome your feedback and benchmark contributions!
