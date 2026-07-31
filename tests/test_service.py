from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from coordinator.errors import AdapterError, FailureKind
from coordinator.local_adapters import DeterministicCurrencyAdapter
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
async def test_all_modes_return_deterministic_decimal_results() -> None:
    provider = StaticRateProvider()
    coordinator = CurrencyCoordinator(
        DeterministicCurrencyAdapter(provider, source="mcp"),
        DeterministicCurrencyAdapter(provider, source="a2a"),
    )

    for mode in BenchmarkMode:
        run = await coordinator.run(request("CAD", "EUR"), mode)
        assert run.succeeded
        assert len(run.results) == 2
        assert run.results[0].primary.converted_amount == Decimal("135.00")

    verified = await coordinator.run(request("CAD"), BenchmarkMode.VERIFIED)
    assert verified.results[0].agreed is True


@pytest.mark.asyncio
async def test_verified_mode_falls_back_when_remote_agent_fails() -> None:
    provider = StaticRateProvider()
    coordinator = CurrencyCoordinator(
        DeterministicCurrencyAdapter(provider, source="mcp"),
        DeterministicCurrencyAdapter(
            provider,
            source="a2a",
            failure=AdapterError(FailureKind.TRANSPORT, "offline"),
        ),
    )

    run = await coordinator.run(request("CAD"), BenchmarkMode.VERIFIED)

    assert run.succeeded
    assert run.failures == {"a2a": "transport: offline"}
    assert "unverified" in run.results[0].warnings[0]


@pytest.mark.asyncio
async def test_disagreement_is_reported() -> None:
    provider = StaticRateProvider()
    coordinator = CurrencyCoordinator(
        DeterministicCurrencyAdapter(provider, source="mcp"),
        DeterministicCurrencyAdapter(
            provider,
            source="a2a",
            rate_multiplier=Decimal("0.9"),
        ),
    )

    run = await coordinator.run(request("CAD"), BenchmarkMode.VERIFIED)

    assert run.results[0].agreed is False
    assert run.results[0].relative_difference == Decimal("0.1")


@pytest.mark.asyncio
async def test_stale_rates_are_flagged() -> None:
    provider = StaticRateProvider(observed_at=datetime.now(UTC) - timedelta(days=2))
    coordinator = CurrencyCoordinator(
        DeterministicCurrencyAdapter(provider, source="mcp"),
        DeterministicCurrencyAdapter(provider, source="a2a"),
    )

    run = await coordinator.run(request("CAD"), BenchmarkMode.MCP_ONLY)

    assert any("stale" in warning for warning in run.results[0].warnings)


@pytest.mark.asyncio
async def test_slow_adapter_hits_the_coordinator_timeout() -> None:
    provider = StaticRateProvider()
    coordinator = CurrencyCoordinator(
        DeterministicCurrencyAdapter(provider, source="mcp", delay_ms=200),
        DeterministicCurrencyAdapter(provider, source="a2a"),
        timeout_seconds=0.01,
    )

    run = await coordinator.run(request("CAD"), BenchmarkMode.MCP_ONLY)

    assert not run.succeeded
    assert run.failures == {"mcp": "timeout: adapter timed out"}


@pytest.mark.asyncio
async def test_unsupported_currency_is_a_typed_provider_failure() -> None:
    provider = StaticRateProvider()
    coordinator = CurrencyCoordinator(
        DeterministicCurrencyAdapter(provider, source="mcp"),
        DeterministicCurrencyAdapter(provider, source="a2a"),
    )

    run = await coordinator.run(request("XYZ"), BenchmarkMode.MCP_ONLY)

    assert not run.succeeded
    assert run.failures["mcp"].startswith("provider:")
