import argparse
import asyncio
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from coordinator.local_adapters import DeterministicCurrencyAdapter
from coordinator.mcp_stdio import McpStdioExchangeRateTool
from coordinator.models import BenchmarkMode, ConversionRequest
from coordinator.providers import StaticRateProvider
from coordinator.service import CurrencyCoordinator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local currency interoperability benchmark")
    parser.add_argument("amount", help="positive decimal amount")
    parser.add_argument("source_currency")
    parser.add_argument("target_currencies", nargs="+")
    parser.add_argument("--mode", choices=[mode.value for mode in BenchmarkMode], default="verified")
    parser.add_argument(
        "--transport",
        choices=["in-process", "mcp-stdio"],
        default="in-process",
        help="rate-tool transport: in-process fixture or the local MCP stdio server",
    )
    parser.add_argument(
        "--a2a-endpoint",
        default=None,
        metavar="URL",
        help="base URL of a live A2A v1.0 currency agent; defaults to the in-process fixture",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=10,
        help="per-adapter timeout; raise for remote endpoints with cold starts",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


async def _run(args: argparse.Namespace) -> int:
    try:
        request = ConversionRequest(
            amount=Decimal(args.amount),
            source_currency=args.source_currency,
            target_currencies=args.target_currencies,
        )
    except (InvalidOperation, ValidationError, ValueError) as exc:
        print(f"invalid request: {exc}")
        return 2

    provider = StaticRateProvider()
    rate_tool = (
        McpStdioExchangeRateTool()
        if args.transport == "mcp-stdio"
        else DeterministicCurrencyAdapter(provider, source="local-mcp")
    )
    if args.a2a_endpoint:
        from coordinator.a2a_remote import A2ARemoteCurrencyAgent
        from coordinator.aws_identity import signer_from_env

        # Set CURRENCY_AWS_ROLE_ARN to sign requests to a deployed AgentCore
        # worker; unset means an unauthenticated agent (e.g. a local worker).
        remote_agent = A2ARemoteCurrencyAgent(
            args.a2a_endpoint, request_signer=signer_from_env()
        )
    else:
        remote_agent = DeterministicCurrencyAdapter(provider, source="local-a2a")
    coordinator = CurrencyCoordinator(rate_tool, remote_agent, timeout_seconds=args.timeout_seconds)
    run = await coordinator.run(request, BenchmarkMode(args.mode))
    if args.as_json:
        print(run.model_dump_json(indent=2))
    else:
        for result in run.results:
            quote = result.primary
            suffix = " verified" if result.agreed else ""
            print(
                f"{quote.amount} {quote.source_currency} = "
                f"{quote.converted_amount.normalize()} {quote.target_currency}"
                f" @ {quote.rate.normalize()} [{quote.source}]{suffix}"
            )
            for warning in result.warnings:
                print(f"warning: {warning}")
        for adapter, failure in run.failures.items():
            print(f"{adapter} failure: {failure}")
    return 0 if run.succeeded else 1


def main() -> int:
    return asyncio.run(_run(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
