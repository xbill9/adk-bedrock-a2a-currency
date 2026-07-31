"""AgentCore Runtime entrypoint: Bedrock worker for the currency benchmark.

This is the AWS half of the flipped topology. The Google ADK agent on Cloud Run
is the master; this Strands agent is its remote worker, reached over A2A v1.0.
AgentCore Runtime hosts it with ``"protocol": "A2A"``, which proxies JSON-RPC
straight through to the container on port 9000 and serves the agent card at
``/.well-known/agent-card.json``.

Why the A2A server is built from ``a2a-sdk`` directly instead of Strands'
``strands.multiagent.a2a.A2AServer``: the ``strands-agents[a2a]`` extra pins
``a2a-sdk<0.4``, i.e. the A2A v0.3 wire methods, which an ``a2a-sdk`` 1.x
client (the ADK coordinator) cannot call. This is the same version split that
forced A2UI out of the ADK agent -- see the note in ``adk_agent/agent.py``.
Strands is therefore used only as the agent loop, and the v1.0 server surface
is assembled here so both ends speak the same protocol version.

Inbound calls are authorized by AgentCore via the CUSTOM_JWT authorizer
declared in ``agentcore/agentcore.json``: it validates the Google-issued OIDC
token that the Cloud Run coordinator attaches, before the request ever reaches
this process. No credential handling happens here.

The ``coordinator/`` and ``mcp_server/`` packages in this directory are copies
of the repo-root packages, synced by ``infra/sync_app.sh`` before deploy so the
CodeZip bundle is self-contained. Edit the root packages, not the copies.
"""

import json
import os
from decimal import Decimal, InvalidOperation

from a2a.helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from model.load import load_model
from starlette.applications import Starlette
from strands import Agent, tool

from coordinator.errors import AdapterError
from coordinator.local_adapters import DeterministicCurrencyAdapter
from coordinator.models import ConversionRequest
from coordinator.providers import FrankfurterRateProvider, StaticRateProvider

# Mirrors the instruction the ADK agent carried when it was the worker: the
# master parses these lines back into quotes, so any prose breaks the contract.
SYSTEM_PROMPT = (
    "You are a specialized currency-conversion worker agent hosted on Amazon "
    "Bedrock AgentCore. Your sole purpose is to use the convert_currency tool to "
    "answer currency conversion questions. Call the tool once with every "
    "requested target currency, then reply with exactly one JSON object per line "
    'of the form {"source_currency": "<ISO code>", "target_currency": "<ISO code>", '
    '"rate": <decimal>, "converted_amount": <decimal>} and no other text. '
    "Never perform the arithmetic yourself and never reformat the tool's numbers. "
    "If asked about anything other than currency conversion, reply that you "
    "cannot help with that topic."
)

PUBLIC_URL = os.getenv("AGENTCORE_A2A_PUBLIC_URL", "http://127.0.0.1:9000")


def _rate_adapter():
    """Pick the worker's rate source; live Frankfurter unless told otherwise."""
    if os.getenv("CURRENCY_RATE_PROVIDER", "frankfurter").strip().lower() == "frankfurter":
        return DeterministicCurrencyAdapter(
            FrankfurterRateProvider(), source="agentcore-worker-frankfurter"
        )
    return DeterministicCurrencyAdapter(
        StaticRateProvider(), source="agentcore-worker-deterministic-fixture"
    )


@tool
async def convert_currency(
    amount: str, source_currency: str, target_currencies: list[str]
) -> str:
    """Convert an amount from one currency into one or more target currencies.

    Args:
        amount: Positive decimal monetary amount, passed as a string.
        source_currency: Three-letter source currency code.
        target_currencies: One or more three-letter target currency codes.

    Returns:
        JSON lines, one object per target, each with source_currency,
        target_currency, rate, and converted_amount.
    """
    try:
        request = ConversionRequest(
            amount=Decimal(amount),
            source_currency=source_currency,
            target_currencies=target_currencies,
        )
    except (InvalidOperation, ValueError) as exc:
        return json.dumps({"error": "invalid_request", "detail": str(exc)})
    try:
        quotes = await _rate_adapter().convert(request)
    except AdapterError as exc:
        return json.dumps({"error": exc.kind.value, "detail": exc.safe_message()})
    return "\n".join(
        json.dumps(
            {
                "source_currency": quote.source_currency,
                "target_currency": quote.target_currency,
                "rate": str(quote.rate),
                "converted_amount": str(quote.converted_amount),
            }
        )
        for quote in quotes
    )


def build_agent() -> Agent:
    return Agent(
        model=load_model(),
        system_prompt=SYSTEM_PROMPT,
        tools=[convert_currency],
        callback_handler=None,
    )


class CurrencyWorkerExecutor(AgentExecutor):
    """Bridges A2A requests onto one Strands agent turn.

    A fresh Agent per request keeps the worker stateless, which is what the
    benchmark wants: each quote must be independently derived, not influenced
    by a previous conversation turn.
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        prompt = context.get_user_input()
        result = await build_agent().invoke_async(prompt)
        await event_queue.enqueue_event(
            new_text_message(
                str(result),
                context_id=context.context_id,
                task_id=context.task_id,
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("the currency worker does not support cancellation")


AGENT_CARD = AgentCard(
    name="Currency Worker",
    description=(
        "Answers currency conversion questions with structured JSON quotes "
        "backed by live reference rates."
    ),
    version="0.2.0",
    supported_interfaces=[
        AgentInterface(url=PUBLIC_URL, protocol_binding="JSONRPC", protocol_version="1.0")
    ],
    capabilities=AgentCapabilities(streaming=False, push_notifications=False),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    skills=[
        AgentSkill(
            id="convert_currency",
            name="Convert currency",
            description=(
                "Convert an amount between ISO currency codes and return one JSON "
                "quote object per target currency."
            ),
            tags=["currency", "exchange-rate", "benchmark"],
            examples=["Convert 100 USD to EUR and CHF."],
            input_modes=["text/plain"],
            output_modes=["text/plain"],
        )
    ],
)

_request_handler = DefaultRequestHandler(
    agent_executor=CurrencyWorkerExecutor(),
    task_store=InMemoryTaskStore(),
    agent_card=AGENT_CARD,
)

# AgentCore proxies JSON-RPC to the container root, so mount the RPC route at
# "/" and serve the card at the well-known path clients probe for.
app = Starlette(
    routes=[
        *create_agent_card_routes(AGENT_CARD),
        *create_jsonrpc_routes(_request_handler, rpc_url="/"),
    ]
)

if __name__ == "__main__":
    import uvicorn

    # AgentCore's A2A runtime proxies to port 9000; bind all interfaces.
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "9000")))
