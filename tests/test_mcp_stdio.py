from decimal import Decimal

import pytest

from coordinator.errors import AdapterError, FailureKind
from coordinator.local_adapters import DeterministicCurrencyAdapter
from coordinator.mcp_stdio import McpStdioExchangeRateTool
from coordinator.models import BenchmarkMode, ConversionRequest
from coordinator.providers import StaticRateProvider
from coordinator.service import CurrencyCoordinator


def request(*targets: str) -> ConversionRequest:
    return ConversionRequest(
        amount=Decimal(100),
        source_currency="USD",
        target_currencies=list(targets),
    )


@pytest.mark.asyncio
async def test_stdio_round_trip_returns_decimal_quotes() -> None:
    quotes = await McpStdioExchangeRateTool().convert(request("CAD", "EUR"))

    assert [quote.target_currency for quote in quotes] == ["CAD", "EUR"]
    assert quotes[0].converted_amount == Decimal("135.00")
    assert quotes[0].source == "mcp-stdio:deterministic-fixture"
    assert quotes[0].latency_ms > 0


@pytest.mark.asyncio
async def test_stdio_unsupported_currency_raises_typed_error() -> None:
    with pytest.raises(AdapterError) as excinfo:
        await McpStdioExchangeRateTool().convert(request("XYZ"))

    assert excinfo.value.kind is FailureKind.PROVIDER
    assert "unsupported currency" in str(excinfo.value)


@pytest.mark.asyncio
async def test_stdio_spawn_failure_is_a_transport_error() -> None:
    tool = McpStdioExchangeRateTool(("/nonexistent-mcp-server",))

    with pytest.raises(AdapterError) as excinfo:
        await tool.convert(request("CAD"))

    assert excinfo.value.kind is FailureKind.TRANSPORT


@pytest.mark.asyncio
async def test_verified_mode_over_stdio_agrees_with_local_a2a() -> None:
    coordinator = CurrencyCoordinator(
        McpStdioExchangeRateTool(),
        DeterministicCurrencyAdapter(StaticRateProvider(), source="local-a2a"),
    )

    run = await coordinator.run(request("CAD"), BenchmarkMode.VERIFIED)

    assert run.succeeded
    assert run.failures == {}
    assert run.results[0].agreed is True
