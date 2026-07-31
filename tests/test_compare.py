from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from coordinator.compare import compare_quotes
from coordinator.models import ConversionQuote, ConversionRequest


def quote(source: str, converted: str) -> ConversionQuote:
    return ConversionQuote(
        source=source,
        source_currency="CAD",
        target_currency="USD",
        amount=Decimal(4500),
        rate=Decimal(converted) / Decimal(4500),
        converted_amount=Decimal(converted),
        observed_at=datetime.now(UTC),
        latency_ms=10,
    )


def test_request_normalizes_currency_codes() -> None:
    request = ConversionRequest(
        amount=Decimal(4500),
        source_currency="cad",
        target_currencies=["usd", " eur "],
    )
    assert request.source_currency == "CAD"
    assert request.target_currencies == ["USD", "EUR"]


def test_naive_timestamp_is_coerced_to_utc() -> None:
    naive = quote("mcp", "3300.00").model_copy(update={"observed_at": datetime(2026, 7, 25, tzinfo=UTC)})
    coerced = ConversionQuote.model_validate(naive.model_dump())
    assert coerced.observed_at.tzinfo is not None
    # Age arithmetic against an aware clock must not raise.
    assert (datetime.now(UTC) - coerced.observed_at).total_seconds() > 0


def test_nonpositive_quote_amounts_are_rejected() -> None:
    base = quote("mcp", "3300.00").model_dump()
    for field in ("amount", "converted_amount", "rate"):
        with pytest.raises(ValidationError):
            ConversionQuote.model_validate({**base, field: Decimal(0)})


def test_close_quotes_agree() -> None:
    result = compare_quotes(quote("mcp", "3300.00"), quote("a2a", "3301.00"))
    assert result.agreed is True
    assert result.warnings == []


def test_material_difference_is_flagged() -> None:
    result = compare_quotes(quote("mcp", "3300.00"), quote("a2a", "3200.00"))
    assert result.agreed is False
    assert result.warnings


def test_mismatched_conversions_are_rejected() -> None:
    other = quote("a2a", "3300.00").model_copy(update={"target_currency": "EUR"})
    with pytest.raises(ValueError, match="same conversion"):
        compare_quotes(quote("mcp", "3300.00"), other)


def test_zero_tolerance_requires_identical_results() -> None:
    result = compare_quotes(
        quote("mcp", "3300.00"),
        quote("a2a", "3300.01"),
        tolerance=Decimal(0),
    )
    assert result.agreed is False
