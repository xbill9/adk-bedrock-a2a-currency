"""RemoteCurrencyAgent implementation backed by the a2a-sdk 1.x client.

Speaks A2A v1.0 (a2a-sdk >= 1.0 wire methods) to a remote agent such as the
benchmark ADK agent in ``adk_agent/``, with no coordinator-framework
dependency. The remote agent is prompted to answer with one JSON quote object
per target currency; parsing is kept in pure functions so it can be tested
without a network or credentials.
"""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from time import perf_counter

import httpx
from pydantic import ValidationError

from coordinator.errors import AdapterError, FailureKind
from coordinator.models import ConversionQuote, ConversionRequest

_PROMPT_TEMPLATE = (
    "Convert {amount} {source} to the following currencies: {targets}. "
    "Use your exchange-rate tool for each target. Reply with exactly one JSON "
    'object per target of the form {{"source_currency": "<ISO code>", '
    '"target_currency": "<ISO code>", "rate": <decimal>, '
    '"converted_amount": <decimal>}} and no other text.'
)


def build_prompt(request: ConversionRequest) -> str:
    return _PROMPT_TEMPLATE.format(
        amount=request.amount,
        source=request.source_currency,
        targets=", ".join(request.target_currencies),
    )


def extract_json_objects(text: str) -> list[dict]:
    """Pull every top-level JSON object out of free-form model text."""
    decoder = json.JSONDecoder(parse_float=Decimal, parse_int=Decimal)
    objects: list[dict] = []
    index = 0
    while (start := text.find("{", index)) != -1:
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(obj, dict):
            objects.append(obj)
        index = start + max(end - start, 1)
    return objects


def parse_quotes(
    text: str,
    request: ConversionRequest,
    *,
    source: str,
    latency_ms: float,
    observed_at: datetime,
) -> list[ConversionQuote]:
    """Map the remote agent's JSON reply onto one quote per requested target."""
    candidates: dict[str, dict] = {}
    for obj in extract_json_objects(text):
        target = str(obj.get("target_currency", "")).strip().upper()
        if target:
            candidates[target] = obj

    quotes: list[ConversionQuote] = []
    for target in request.target_currencies:
        obj = candidates.get(target)
        if obj is None:
            raise AdapterError(
                FailureKind.PROTOCOL,
                f"remote agent reply is missing a quote for {target}: {text[:200]!r}",
            )
        try:
            rate = Decimal(str(obj["rate"]))
            converted = Decimal(str(obj.get("converted_amount", request.amount * rate)))
            quotes.append(
                ConversionQuote(
                    source=source,
                    source_currency=request.source_currency,
                    target_currency=target,
                    amount=request.amount,
                    rate=rate,
                    converted_amount=converted,
                    observed_at=observed_at,
                    latency_ms=latency_ms,
                )
            )
        except (KeyError, InvalidOperation, ValidationError) as exc:
            raise AdapterError(
                FailureKind.PROTOCOL,
                f"remote agent quote for {target} is malformed: {exc}",
            ) from exc
    return quotes


class A2ARemoteCurrencyAgent:
    """Calls a remote A2A currency agent and adapts its reply to ConversionQuote."""

    def __init__(
        self,
        endpoint: str,
        *,
        source: str = "adk-a2a",
        timeout_s: float = 120.0,
    ) -> None:
        self._endpoint = endpoint
        self._source = source
        self._timeout_s = timeout_s

    async def _send(self, prompt: str) -> str:
        """Resolve the agent card, send one text message, and gather reply text."""
        from a2a.client import A2ACardResolver, ClientConfig, create_client
        from a2a.helpers import get_message_text, get_text_parts, new_text_message
        from a2a.types import Role, SendMessageRequest

        # local_address pins the socket to IPv4; IPv6 to some hosts hangs in
        # some sandboxes (same workaround as FrankfurterRateProvider).
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
        async with httpx.AsyncClient(timeout=self._timeout_s, transport=transport) as httpx_client:
            resolver = A2ACardResolver(httpx_client=httpx_client, base_url=self._endpoint)
            card = await resolver.get_agent_card()
            # ADK's to_a2a() advertises the server's bind address (e.g.
            # http://127.0.0.1:8080) on the card, and the a2a-sdk client
            # routes transport by card URL. Rewrite the interfaces to the
            # endpoint we were configured with.
            for interface in card.supported_interfaces:
                interface.url = self._endpoint
            client = await create_client(
                agent=card,
                client_config=ClientConfig(streaming=False, httpx_client=httpx_client),
            )
            try:
                message_request = SendMessageRequest(
                    message=new_text_message(prompt, role=Role.ROLE_USER)
                )
                texts: list[str] = []
                async for chunk in client.send_message(message_request):
                    if chunk.HasField("message"):
                        texts.append(get_message_text(chunk.message))
                    elif chunk.HasField("task"):
                        for artifact in chunk.task.artifacts:
                            texts.append("\n".join(get_text_parts(artifact.parts)))
                return "\n".join(text for text in texts if text)
            finally:
                await client.close()

    async def convert(self, request: ConversionRequest) -> list[ConversionQuote]:
        started = perf_counter()
        try:
            response_text = await asyncio.wait_for(
                self._send(build_prompt(request)), timeout=self._timeout_s
            )
        except TimeoutError as exc:
            raise AdapterError(
                FailureKind.TIMEOUT,
                f"A2A agent at {self._endpoint} exceeded {self._timeout_s}s",
            ) from exc
        except httpx.HTTPStatusError as exc:
            kind = (
                FailureKind.AUTHENTICATION
                if exc.response.status_code in (401, 403)
                else FailureKind.TRANSPORT
            )
            raise AdapterError(kind, f"A2A endpoint returned {exc.response.status_code}") from exc
        except (httpx.TransportError, ConnectionError, OSError) as exc:
            raise AdapterError(
                FailureKind.TRANSPORT,
                f"cannot reach A2A endpoint {self._endpoint}: {exc}",
            ) from exc
        except Exception as exc:  # a2a-sdk raises protocol errors of varied types
            raise AdapterError(
                FailureKind.PROTOCOL,
                f"A2A protocol failure from {self._endpoint}: {exc}",
            ) from exc

        latency_ms = (perf_counter() - started) * 1000
        return parse_quotes(
            response_text,
            request,
            source=self._source,
            latency_ms=latency_ms,
            observed_at=datetime.now(UTC),
        )
