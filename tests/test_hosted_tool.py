import json

import pytest

from coordinator.hosted_tool import run_currency_benchmark


def test_hosted_tool_description_does_not_label_all_results_as_fixtures() -> None:
    description = run_currency_benchmark.__doc__ or ""

    assert "using deterministic local fixture rates" not in description
    assert "source fields identify live versus" in description


@pytest.mark.asyncio
async def test_hosted_tool_returns_structured_verified_result() -> None:
    payload = json.loads(
        await run_currency_benchmark("100", "usd", ["cad", "eur"], "verified")
    )

    assert payload["mode"] == "verified"
    assert len(payload["results"]) == 2
    assert payload["results"][0]["agreed"] is True


@pytest.mark.asyncio
async def test_hosted_tool_returns_structured_validation_error() -> None:
    payload = json.loads(await run_currency_benchmark("-1", "USD", ["CAD"]))

    assert payload["error"] == "invalid_request"


@pytest.mark.asyncio
async def test_hosted_master_fails_closed_when_gcp_adk_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CURRENCY_A2A_ENDPOINT", raising=False)
    monkeypatch.setenv("CURRENCY_REQUIRE_GCP_ADK", "1")

    payload = json.loads(await run_currency_benchmark("100", "USD", ["CAD"], "a2a_only"))

    assert payload["error"] == "gcp_adk_not_configured"


@pytest.mark.asyncio
async def test_mcp_baseline_does_not_require_gcp_adk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CURRENCY_A2A_ENDPOINT", raising=False)
    monkeypatch.setenv("CURRENCY_REQUIRE_GCP_ADK", "true")

    payload = json.loads(await run_currency_benchmark("100", "USD", ["CAD"], "mcp_only"))

    assert payload["mode"] == "mcp_only"
    assert payload["results"][0]["primary"]["source"] == "hosted-local-mcp"
