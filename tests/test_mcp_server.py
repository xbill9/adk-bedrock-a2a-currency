import json
from decimal import Decimal

import pytest

from coordinator.providers import StaticRateProvider
from mcp_server.server import dispatch


@pytest.mark.asyncio
async def test_mcp_tool_discovery() -> None:
    response = await dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        StaticRateProvider(),
    )
    assert response is not None
    assert response["result"]["tools"][0]["name"] == "convert_currency"


@pytest.mark.asyncio
async def test_mcp_tool_call_returns_structured_decimal_result() -> None:
    response = await dispatch(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "convert_currency",
                "arguments": {
                    "amount": "10.25",
                    "source_currency": "usd",
                    "target_currency": "cad",
                },
            },
        },
        StaticRateProvider(),
    )
    assert response is not None
    result = response["result"]["structuredContent"]
    assert Decimal(result["converted_amount"]) == Decimal("13.8375")
    assert json.loads(response["result"]["content"][0]["text"]) == result


@pytest.mark.asyncio
async def test_mcp_unsupported_currency_is_an_in_band_tool_error() -> None:
    response = await dispatch(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "convert_currency",
                "arguments": {
                    "amount": "10",
                    "source_currency": "USD",
                    "target_currency": "XYZ",
                },
            },
        },
        StaticRateProvider(),
    )
    assert response is not None
    assert "error" not in response
    assert response["result"]["isError"] is True
    assert "unsupported currency" in response["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_mcp_notifications_get_no_response() -> None:
    response = await dispatch(
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        StaticRateProvider(),
    )
    assert response is None


@pytest.mark.asyncio
async def test_mcp_invalid_arguments_return_json_rpc_error() -> None:
    response = await dispatch(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "convert_currency",
                "arguments": {
                    "amount": "-1",
                    "source_currency": "USD",
                    "target_currency": "CAD",
                },
            },
        },
        StaticRateProvider(),
    )
    assert response is not None
    assert response["error"]["code"] == -32602
