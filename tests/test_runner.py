from pathlib import Path

import pytest

from evaluations.runner import load_cases, run_evaluation, summarize

CASES_PATH = Path(__file__).resolve().parent.parent / "evaluations" / "cases.jsonl"


@pytest.mark.asyncio
async def test_full_matrix_passes_and_summarizes(tmp_path: Path) -> None:
    output = tmp_path / "results.jsonl"
    records = await run_evaluation(CASES_PATH, output)

    assert len(records) == len(load_cases(CASES_PATH)) * 3
    assert all(record["success"] for record in records), [
        record for record in records if not record["success"]
    ]
    assert len(output.read_text(encoding="utf-8").splitlines()) == len(records)

    summary = summarize(records)
    assert summary["total_records"] == len(records)
    for mode_stats in summary["modes"].values():
        assert mode_stats["success_rate"] == 1.0
        assert mode_stats["median_latency_ms"] is not None
        assert mode_stats["p95_latency_ms"] >= mode_stats["median_latency_ms"]
    assert summary["modes"]["verified"]["agreement_rate"] is not None


@pytest.mark.asyncio
async def test_disagreement_case_only_warns_in_verified_mode(tmp_path: Path) -> None:
    records = await run_evaluation(CASES_PATH, tmp_path / "results.jsonl")
    disagreement = [r for r in records if r["case_id"] == "a2a-disagreement"]

    assert len(disagreement) == 3
    verified = next(r for r in disagreement if r["mode"] == "verified")
    assert verified["agreements"] == [False]
    assert any("differ" in warning for warning in verified["warnings"])
