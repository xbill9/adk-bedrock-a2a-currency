from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class BenchmarkMode(StrEnum):
    MCP_ONLY = "mcp_only"
    A2A_ONLY = "a2a_only"
    VERIFIED = "verified"


class ConversionRequest(BaseModel):
    amount: Decimal = Field(gt=0)
    source_currency: str
    target_currencies: list[str] = Field(min_length=1)

    @field_validator("source_currency")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        return _iso_code(value)

    @field_validator("target_currencies")
    @classmethod
    def normalize_targets(cls, values: list[str]) -> list[str]:
        normalized = [_iso_code(value) for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("target currencies must be unique")
        return normalized


class ConversionQuote(BaseModel):
    source: str
    source_currency: str
    target_currency: str
    amount: Decimal = Field(gt=0)
    rate: Decimal = Field(gt=0)
    converted_amount: Decimal = Field(gt=0)
    observed_at: datetime
    latency_ms: float = Field(ge=0)

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Treat a timestamp without an offset as UTC so age math cannot crash."""
        return value if value.tzinfo else value.replace(tzinfo=UTC)


class VerificationResult(BaseModel):
    mode: BenchmarkMode
    primary: ConversionQuote
    verifier: ConversionQuote | None = None
    relative_difference: Decimal | None = None
    agreed: bool | None = None
    warnings: list[str] = Field(default_factory=list)


class ConversionRun(BaseModel):
    """Stable result envelope shared by the CLI and future hosted adapters."""

    mode: BenchmarkMode
    request: ConversionRequest
    results: list[VerificationResult] = Field(default_factory=list)
    failures: dict[str, str] = Field(default_factory=dict)
    elapsed_ms: float = Field(ge=0)

    @property
    def succeeded(self) -> bool:
        return bool(self.results)


def _iso_code(value: str) -> str:
    result = value.strip().upper()
    if len(result) != 3 or not result.isalpha():
        raise ValueError(f"invalid ISO 4217-style currency code: {value!r}")
    return result
