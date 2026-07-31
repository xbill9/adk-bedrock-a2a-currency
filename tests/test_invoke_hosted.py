import pytest

from evaluations import invoke_hosted


def test_auth_headers_omitted_for_public_service() -> None:
    assert invoke_hosted.auth_headers(None) == {}
    assert invoke_hosted.auth_headers("") == {}


def test_auth_headers_carry_bearer_token_for_private_service() -> None:
    assert invoke_hosted.auth_headers("id-token") == {"Authorization": "Bearer id-token"}


def test_main_requires_endpoint_and_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CURRENCY_COORDINATOR_ENDPOINT", raising=False)
    monkeypatch.setattr(invoke_hosted.sys, "argv", ["invoke_hosted", "Convert 1 USD to EUR."])

    assert invoke_hosted.main() == 2


def test_main_requires_prompt_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURRENCY_COORDINATOR_ENDPOINT", "https://coordinator.example")
    monkeypatch.setattr(invoke_hosted.sys, "argv", ["invoke_hosted"])

    assert invoke_hosted.main() == 2


def test_main_reports_transport_failure_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CURRENCY_COORDINATOR_ENDPOINT", "https://coordinator.example")
    monkeypatch.setattr(invoke_hosted.sys, "argv", ["invoke_hosted", "Convert 1 USD to EUR."])

    async def boom(*args, **kwargs):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(invoke_hosted, "send", boom)

    assert invoke_hosted.main() == 1
