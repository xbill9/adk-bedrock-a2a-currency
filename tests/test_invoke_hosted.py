from evaluations.invoke_hosted import extract_result_text, response_diagnostic


def test_extract_result_text_returns_result() -> None:
    assert extract_result_text({"result": "two verified quotes"}) == ["two verified quotes"]


def test_extract_result_text_rejects_empty_or_missing_result() -> None:
    assert extract_result_text({}) == []
    assert extract_result_text({"result": ""}) == []
    assert extract_result_text({"result": None}) == []


def test_extract_result_text_rejects_non_string_result() -> None:
    assert extract_result_text({"result": {"nested": "object"}}) == []


def test_response_diagnostic_exposes_only_failure_metadata() -> None:
    diagnostic = response_diagnostic(
        {
            "error": "invalid_request",
            "detail": "payload must include a 'prompt' string",
            "internal": "sensitive",
        }
    )

    assert diagnostic == {
        "error": "invalid_request",
        "detail": "payload must include a 'prompt' string",
        "keys": ["detail", "error", "internal"],
    }
