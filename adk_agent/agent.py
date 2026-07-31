"""Minimal ADK currency agent served over A2A v1.0 for the benchmark.

Derived from xbill9/currency-agent@aeef3c4 with the A2UI extension removed:
a2ui-agent-sdk pins a2a-sdk<0.4 (A2A v0.3.0 wire methods), which A2A v1.0
clients (a2a-sdk>=1.0, e.g. the Bedrock coordinator's adapter) cannot call.
Removing A2UI lets this agent run google-adk 2.5.0 with a2a-sdk 1.x and answer
in plain text parts, which is also what the benchmark needs to parse quotes.
"""

import logging
import os

from dotenv import load_dotenv
from google.adk.a2a.utils.agent_to_a2a import to_a2a
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams
from starlette.responses import JSONResponse

logging.basicConfig(format="[%(levelname)s]: %(message)s", level=logging.INFO)

load_dotenv()

INSTRUCTION = (
    "You are a specialized assistant for currency conversions. "
    "Your sole purpose is to use the 'get_exchange_rate' tool to answer questions about "
    "currency exchange rates. "
    "When asked to convert an amount, call the tool for each requested target currency, then "
    "reply with exactly one JSON object per line of the form "
    '{"source_currency": "<ISO code>", "target_currency": "<ISO code>", '
    '"rate": <decimal>, "converted_amount": <decimal>} '
    "and no other text. "
    "If the user asks about anything other than currency conversion or exchange rates, "
    "politely state that you cannot help with that topic."
)

root_agent = LlmAgent(
    model=os.getenv("GENAI_MODEL", "gemini-2.5-flash"),
    name="currency_agent",
    description="An agent that answers currency conversion questions with structured quotes",
    instruction=INSTRUCTION,
    tools=[
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8081/mcp")
            ),
        ),
    ],
)

a2a_app = to_a2a(
    root_agent,
    host=os.getenv("HOST", "127.0.0.1"),
    port=int(os.getenv("PORT", "10001")),
)


async def health(request):
    return JSONResponse({"status": "ok"})


a2a_app.add_route("/health", health, methods=["GET"])
