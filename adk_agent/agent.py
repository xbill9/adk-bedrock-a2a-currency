"""Google ADK master agent for the currency interoperability benchmark.

This is the GCP half of the flipped topology. The ADK agent is now the
coordinator: it owns the benchmark modes, calls the MCP exchange-rate tool
directly, and delegates independent verification to the Strands worker hosted
on Amazon Bedrock AgentCore Runtime over A2A v1.0.

Authentication to AgentCore is keyless. Cloud Run's metadata server mints a
Google-issued OIDC token for the runtime service account, and AgentCore's
CUSTOM_JWT authorizer validates it against Google's discovery document. The
container holds no AWS credentials; see ``coordinator/gcp_identity.py``.

The ``coordinator/`` package in this directory is a copy of the repo-root
package, synced by ``infra/sync_adk.sh`` before deploy so the Cloud Run source
upload is self-contained. Edit the root package, not the copy.
"""

import logging
import os

from dotenv import load_dotenv
from google.adk.a2a.utils.agent_to_a2a import to_a2a
from google.adk.agents import LlmAgent
from starlette.responses import JSONResponse

from coordinator.hosted_tool import run_currency_benchmark

logging.basicConfig(format="[%(levelname)s]: %(message)s", level=logging.INFO)

load_dotenv()

# Carried over from the Strands master prompt this replaces: the model routes
# and reports, it never does arithmetic. Source labels are the only evidence of
# whether a result is live.
INSTRUCTION = (
    "You are the Google ADK master agent for a currency interoperability "
    "benchmark, not a general chatbot. The Amazon Bedrock AgentCore worker is "
    "your remote agent and is reached by the benchmark tool over A2A. "
    "For every conversion request, call run_currency_benchmark. Never calculate or "
    "verify arithmetic yourself. Determine whether results are live only from each "
    "returned source field: sources containing 'deterministic-fixture' or "
    "'hosted-local' are non-live; do not label other sources as fixtures. Preserve "
    "the returned amounts, rates, timestamps, failure labels, and warnings exactly. "
    "Parse 'Convert 100 USD to EUR' as amount='100', source_currency='USD', and "
    "target_currencies=['EUR']; every currency listed after 'to' is a target. "
    "Never ask the user to confirm information already present in the request. "
    "Ask for amount, source currency, or target currencies only when truly missing."
)

root_agent = LlmAgent(
    model=os.getenv("GENAI_MODEL", "gemini-2.5-flash"),
    name="currency_coordinator",
    description=(
        "Master agent that benchmarks MCP tool calls against an AWS-hosted "
        "remote agent and returns structured, verified conversion results"
    ),
    instruction=INSTRUCTION,
    tools=[run_currency_benchmark],
)

a2a_app = to_a2a(
    root_agent,
    host=os.getenv("HOST", "127.0.0.1"),
    port=int(os.getenv("PORT", "8080")),
)


async def health(request):
    return JSONResponse({"status": "ok"})


a2a_app.add_route("/health", health, methods=["GET"])
