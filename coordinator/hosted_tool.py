"""Function tool exposed to the hosted coordinator.

Defaults to deterministic fixture adapters so local tests run without credentials.
Hosted deployments set ``CURRENCY_REQUIRE_AWS_AGENTCORE=1`` so a missing
``CURRENCY_A2A_ENDPOINT`` fails closed instead of silently replacing the AWS
AgentCore worker with a fixture.

Requests to a deployed worker are SigV4-signed with credentials federated from
the coordinator's Google identity; see ``coordinator/aws_identity.py``.
"""

import json
import os
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from coordinator.local_adapters import DeterministicCurrencyAdapter
from coordinator.models import BenchmarkMode, ConversionRequest
from coordinator.providers import StaticRateProvider
from coordinator.service import CurrencyCoordinator


async def run_currency_benchmark(
    amount: str,
    source_currency: str,
    target_currencies: list[str],
    mode: str = "verified",
) -> str:
    """Run one currency interoperability benchmark mode.

    Args:
        amount: Positive decimal monetary amount, passed as a string.
        source_currency: Three-letter source currency code.
        target_currencies: One or more three-letter target currency codes.
        mode: mcp_only, a2a_only, or verified.

    Returns:
        A structured JSON result whose source fields identify live versus
        deterministic-fixture adapters. Treat only sources containing
        "deterministic-fixture" or "hosted-local" as non-live.
    """
    try:
        request = ConversionRequest(
            amount=Decimal(amount),
            source_currency=source_currency,
            target_currencies=target_currencies,
        )
        benchmark_mode = BenchmarkMode(mode)
    except (InvalidOperation, ValidationError, ValueError) as exc:
        return json.dumps({"error": "invalid_request", "detail": str(exc)})

    provider = StaticRateProvider()
    if os.getenv("CURRENCY_RATE_TRANSPORT") == "mcp-stdio":
        from coordinator.mcp_stdio import McpStdioExchangeRateTool

        rate_tool = McpStdioExchangeRateTool()
    else:
        rate_tool = DeterministicCurrencyAdapter(provider, source="hosted-local-mcp")
    a2a_endpoint = os.getenv("CURRENCY_A2A_ENDPOINT", "").strip()
    require_agentcore = os.getenv("CURRENCY_REQUIRE_AWS_AGENTCORE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if a2a_endpoint:
        from coordinator.a2a_remote import A2ARemoteCurrencyAgent
        from coordinator.aws_identity import signer_from_env

        # AgentCore's AWS_IAM authorizer expects SigV4; None here means an
        # unauthenticated endpoint (a local worker in tests).
        remote_agent = A2ARemoteCurrencyAgent(
            a2a_endpoint,
            source="aws-agentcore-a2a-worker",
            request_signer=signer_from_env(),
        )
    elif require_agentcore and benchmark_mode is not BenchmarkMode.MCP_ONLY:
        return json.dumps(
            {
                "error": "agentcore_not_configured",
                "detail": (
                    "CURRENCY_A2A_ENDPOINT is required when "
                    "CURRENCY_REQUIRE_AWS_AGENTCORE is enabled"
                ),
            }
        )
    else:
        remote_agent = DeterministicCurrencyAdapter(provider, source="hosted-local-a2a")
    coordinator = CurrencyCoordinator(
        rate_tool,
        remote_agent,
        timeout_seconds=float(os.getenv("CURRENCY_TIMEOUT_SECONDS", "10")),
    )
    result = await coordinator.run(request, benchmark_mode)
    return result.model_dump_json()
