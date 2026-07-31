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


def test_live_a2a_endpoint_picks_up_a_configured_request_signer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tier 3 in docs/E2E_TESTING.md depends on this: the SigV4 signer must
    reach the A2A client, otherwise a deployed worker rejects every call."""
    monkeypatch.setenv("CURRENCY_AWS_ROLE_ARN", "arn:aws:iam::123456789012:role/r")
    monkeypatch.setenv("CURRENCY_A2A_AUDIENCE", "currencybench-agentcore-worker")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    captured = {}

    from coordinator import a2a_remote

    class Recorder(a2a_remote.A2ARemoteCurrencyAgent):
        def __init__(self, endpoint, **kwargs):
            captured["request_signer"] = kwargs.get("request_signer")
            super().__init__(endpoint, **kwargs)

    monkeypatch.setattr(a2a_remote, "A2ARemoteCurrencyAgent", Recorder)

    args = build_parser().parse_args(
        ["100", "USD", "EUR", "--mode", "mcp_only", "--a2a-endpoint", "https://worker.example"]
    )
    asyncio.run(_run(args))

    from coordinator.aws_identity import SigV4Auth

    assert isinstance(captured["request_signer"], SigV4Auth)


def test_local_unauthenticated_worker_needs_no_signer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CURRENCY_AWS_ROLE_ARN", raising=False)
    captured = {}

    from coordinator import a2a_remote

    class Recorder(a2a_remote.A2ARemoteCurrencyAgent):
        def __init__(self, endpoint, **kwargs):
            captured["request_signer"] = kwargs.get("request_signer")
            super().__init__(endpoint, **kwargs)

    monkeypatch.setattr(a2a_remote, "A2ARemoteCurrencyAgent", Recorder)

    args = build_parser().parse_args(
        ["100", "USD", "EUR", "--mode", "mcp_only", "--a2a-endpoint", "http://127.0.0.1:9000"]
    )
    asyncio.run(_run(args))

    assert captured["request_signer"] is None
