"""Stable application interfaces for preview SDK integrations.

Implement these protocols in separate modules once the current Strands Agents,
Bedrock AgentCore hosting, A2A, and MCP SDK versions have been validated.
"""

from typing import Protocol

from coordinator.models import ConversionQuote, ConversionRequest


class ExchangeRateTool(Protocol):
    async def convert(self, request: ConversionRequest) -> list[ConversionQuote]: ...


class RemoteCurrencyAgent(Protocol):
    async def convert(self, request: ConversionRequest) -> list[ConversionQuote]: ...

