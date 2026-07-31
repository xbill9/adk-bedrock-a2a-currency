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
