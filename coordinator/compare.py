from decimal import Decimal

from coordinator.models import BenchmarkMode, ConversionQuote, VerificationResult


def compare_quotes(
    primary: ConversionQuote,
    verifier: ConversionQuote,
    *,
    tolerance: Decimal = Decimal("0.005"),
) -> VerificationResult:
    """Compare quotes using relative difference rather than display rounding."""
    if (
        primary.source_currency != verifier.source_currency
        or primary.target_currency != verifier.target_currency
        or primary.amount != verifier.amount
    ):
        raise ValueError("quotes do not describe the same conversion")

    denominator = abs(primary.converted_amount)
    difference = abs(primary.converted_amount - verifier.converted_amount)
    relative = difference / denominator if denominator else Decimal(0)
    agreed = relative <= tolerance

    warnings: list[str] = []
    if not agreed:
        warnings.append(
            f"independent results differ by {relative:.4%}; do not treat this as a final quote"
        )

    return VerificationResult(
        mode=BenchmarkMode.VERIFIED,
        primary=primary,
        verifier=verifier,
        relative_difference=relative,
        agreed=agreed,
        warnings=warnings,
    )

