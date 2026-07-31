from datetime import UTC, datetime
from decimal import Decimal

import pytest

from coordinator.a2a_remote import build_prompt, extract_json_objects, parse_quotes
from coordinator.errors import AdapterError, FailureKind
from coordinator.models import ConversionRequest

OBSERVED = datetime(2026, 7, 27, tzinfo=UTC)


def request(*targets: str) -> ConversionRequest:
    return ConversionRequest(amount=Decimal(100), source_currency="USD", target_currencies=list(targets))


def test_build_prompt_lists_all_targets():
    prompt = build_prompt(request("EUR", "JPY"))
    assert "100 USD" in prompt
    assert "EUR, JPY" in prompt


def test_extract_json_objects_handles_fences_and_prose():
    text = 'Sure! ```json\n{"a": 1}\n``` and also {"b": 2.5} trailing'
    objects = extract_json_objects(text)
    assert objects == [{"a": Decimal(1)}, {"b": Decimal("2.5")}]


def test_parse_quotes_maps_each_target():
    text = (
        '{"source_currency": "USD", "target_currency": "EUR", "rate": 0.87804, "converted_amount": 87.804}\n'
        '{"source_currency": "USD", "target_currency": "JPY", "rate": 147.5, "converted_amount": 14750}'
    )
    quotes = parse_quotes(
        text, request("EUR", "JPY"), source="adk-a2a", latency_ms=12.5, observed_at=OBSERVED
    )
    assert [q.target_currency for q in quotes] == ["EUR", "JPY"]
    assert quotes[0].rate == Decimal("0.87804")
    assert quotes[1].converted_amount == Decimal(14750)
    assert all(q.source == "adk-a2a" for q in quotes)


def test_parse_quotes_computes_missing_converted_amount():
    text = '{"target_currency": "EUR", "rate": 0.9}'
    quotes = parse_quotes(
        text, request("EUR"), source="adk-a2a", latency_ms=1.0, observed_at=OBSERVED
    )
    assert quotes[0].converted_amount == Decimal(90)


def test_parse_quotes_missing_target_is_protocol_failure():
    text = '{"target_currency": "EUR", "rate": 0.9, "converted_amount": 90}'
    with pytest.raises(AdapterError) as excinfo:
        parse_quotes(text, request("EUR", "JPY"), source="adk-a2a", latency_ms=1.0, observed_at=OBSERVED)
    assert excinfo.value.kind is FailureKind.PROTOCOL


def test_parse_quotes_rejects_malformed_rate():
    text = '{"target_currency": "EUR", "rate": "not-a-number", "converted_amount": 90}'
    with pytest.raises(AdapterError) as excinfo:
        parse_quotes(text, request("EUR"), source="adk-a2a", latency_ms=1.0, observed_at=OBSERVED)
    assert excinfo.value.kind is FailureKind.PROTOCOL


def test_parse_quotes_rejects_conversational_refusal():
    with pytest.raises(AdapterError) as excinfo:
        parse_quotes(
            "I can only help with currency conversions.",
            request("EUR"),
            source="adk-a2a",
            latency_ms=1.0,
            observed_at=OBSERVED,
        )
    assert excinfo.value.kind is FailureKind.PROTOCOL
