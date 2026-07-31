import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from time import perf_counter

from coordinator.adapters import ExchangeRateTool, RemoteCurrencyAgent
from coordinator.compare import compare_quotes
from coordinator.errors import AdapterError, FailureKind
from coordinator.models import (
    BenchmarkMode,
    ConversionRequest,
    ConversionRun,
    VerificationResult,
)


class CurrencyCoordinator:
    def __init__(
        self,
        exchange_rate_tool: ExchangeRateTool,
        remote_agent: RemoteCurrencyAgent,
        *,
        tolerance: Decimal = Decimal("0.005"),
        stale_after_seconds: int = 86_400,
        timeout_seconds: float = 10,
    ) -> None:
        self._mcp = exchange_rate_tool
        self._a2a = remote_agent
        self._tolerance = tolerance
        self._stale_after_seconds = stale_after_seconds
        self._timeout_seconds = timeout_seconds

    async def run(self, request: ConversionRequest, mode: BenchmarkMode) -> ConversionRun:
        started = perf_counter()
        failures: dict[str, str] = {}
        results: list[VerificationResult] = []

        if mode is BenchmarkMode.MCP_ONLY:
            quotes = await self._call("mcp", self._mcp, request, failures)
            results = [self._single(mode, quote) for quote in quotes]
        elif mode is BenchmarkMode.A2A_ONLY:
            quotes = await self._call("a2a", self._a2a, request, failures)
            results = [self._single(mode, quote) for quote in quotes]
        else:
            mcp_task = self._call("mcp", self._mcp, request, failures)
            a2a_task = self._call("a2a", self._a2a, request, failures)
            mcp_quotes, a2a_quotes = await asyncio.gather(mcp_task, a2a_task)
            results = self._verified(request, mcp_quotes, a2a_quotes)

        for result in results:
            self._append_staleness_warning(result)
        return ConversionRun(
            mode=mode,
            request=request,
            results=results,
            failures=failures,
            elapsed_ms=(perf_counter() - started) * 1000,
        )

    async def _call(
        self,
        name: str,
        adapter: ExchangeRateTool | RemoteCurrencyAgent,
        request: ConversionRequest,
        failures: dict[str, str],
    ):
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await adapter.convert(request)
        except TimeoutError:
            failures[name] = AdapterError(FailureKind.TIMEOUT, "adapter timed out").safe_message()
        except AdapterError as exc:
            failures[name] = exc.safe_message()
        except Exception as exc:  # noqa: BLE001 - adapter boundary converts SDK failures
            failures[name] = AdapterError(FailureKind.PROTOCOL, str(exc)).safe_message()
        return []

    def _verified(self, request, mcp_quotes, a2a_quotes) -> list[VerificationResult]:
        mcp_by_target = {quote.target_currency: quote for quote in mcp_quotes}
        a2a_by_target = {quote.target_currency: quote for quote in a2a_quotes}
        results: list[VerificationResult] = []
        for target in request.target_currencies:
            primary = mcp_by_target.get(target)
            verifier = a2a_by_target.get(target)
            if primary and verifier:
                results.append(compare_quotes(primary, verifier, tolerance=self._tolerance))
            elif primary or verifier:
                quote = primary or verifier
                assert quote is not None
                result = self._single(BenchmarkMode.VERIFIED, quote)
                result.warnings.append(
                    "independent verification unavailable; result is unverified"
                )
                results.append(result)
        return results

    @staticmethod
    def _single(mode: BenchmarkMode, quote) -> VerificationResult:
        return VerificationResult(mode=mode, primary=quote)

    def _append_staleness_warning(self, result: VerificationResult) -> None:
        now = datetime.now(UTC)
        quotes = [result.primary] + ([result.verifier] if result.verifier else [])
        if any((now - quote.observed_at).total_seconds() > self._stale_after_seconds for quote in quotes):
            result.warnings.append("rate is stale; check the observation timestamp")
