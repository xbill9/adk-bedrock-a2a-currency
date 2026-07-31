import asyncio

import pytest

from coordinator.cli import _run, build_parser


@pytest.mark.asyncio
async def test_non_numeric_amount_is_rejected_not_crashed(capsys) -> None:
    args = build_parser().parse_args(["abc", "USD", "CAD"])

    assert await _run(args) == 2
    assert "invalid request" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_verified_run_prints_quotes(capsys) -> None:
    args = build_parser().parse_args(["100", "usd", "cad"])

    assert await _run(args) == 0
    assert "100 USD = 135 CAD" in capsys.readouterr().out


def test_live_a2a_endpoint_picks_up_a_configured_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tier 3 in docs/E2E_TESTING.md depends on this: a hand-minted token must
    reach the A2A client, otherwise a protected worker rejects every call."""
    monkeypatch.setenv("CURRENCY_A2A_BEARER_TOKEN", "hand-minted")
    captured = {}

    from coordinator import a2a_remote

    class Recorder(a2a_remote.A2ARemoteCurrencyAgent):
        def __init__(self, endpoint, **kwargs):
            captured["token_provider"] = kwargs.get("token_provider")
            super().__init__(endpoint, **kwargs)

    monkeypatch.setattr(a2a_remote, "A2ARemoteCurrencyAgent", Recorder)

    args = build_parser().parse_args(
        ["100", "USD", "EUR", "--mode", "mcp_only", "--a2a-endpoint", "https://worker.example"]
    )
    asyncio.run(_run(args))

    assert captured["token_provider"] is not None
    assert asyncio.run(captured["token_provider"].token()) == "hand-minted"
