import httpx
import pytest

from coordinator.errors import AdapterError, FailureKind
from coordinator.gcp_identity import (
    GoogleIdTokenProvider,
    StaticTokenProvider,
    token_provider_from_env,
)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.asyncio
async def test_static_provider_returns_its_value() -> None:
    assert await StaticTokenProvider("abc").token() == "abc"


def test_audience_is_required() -> None:
    with pytest.raises(ValueError):
        GoogleIdTokenProvider("")


@pytest.mark.asyncio
async def test_token_is_fetched_with_the_metadata_flavor_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["flavor"] = request.headers.get("Metadata-Flavor")
        return httpx.Response(200, text="header.payload.signature\n")

    _patch_transport(monkeypatch, handler)
    provider = GoogleIdTokenProvider(
        "test-audience", metadata_url="http://metadata.test/token", now=_FakeClock()
    )

    assert await provider.token() == "header.payload.signature"
    assert seen["flavor"] == "Google"
    assert "audience=test-audience" in seen["url"]


@pytest.mark.asyncio
async def test_token_is_cached_until_it_nears_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text=f"token-{calls['n']}")

    _patch_transport(monkeypatch, handler)
    clock = _FakeClock()
    provider = GoogleIdTokenProvider(
        "test-audience", metadata_url="http://metadata.test/token", now=clock
    )

    assert await provider.token() == "token-1"
    clock.now = 3000  # still inside the 3600 - 300 s window
    assert await provider.token() == "token-1"
    assert calls["n"] == 1

    clock.now = 3400  # past the refresh margin
    assert await provider.token() == "token-2"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_metadata_rejection_is_classified_as_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    _patch_transport(monkeypatch, handler)
    provider = GoogleIdTokenProvider(
        "test-audience", metadata_url="http://metadata.test/token", now=_FakeClock()
    )

    with pytest.raises(AdapterError) as excinfo:
        await provider.token()
    assert excinfo.value.kind is FailureKind.AUTHENTICATION


@pytest.mark.asyncio
async def test_empty_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="   ")

    _patch_transport(monkeypatch, handler)
    provider = GoogleIdTokenProvider(
        "test-audience", metadata_url="http://metadata.test/token", now=_FakeClock()
    )

    with pytest.raises(AdapterError) as excinfo:
        await provider.token()
    assert excinfo.value.kind is FailureKind.AUTHENTICATION


def test_env_provider_prefers_an_explicit_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURRENCY_A2A_BEARER_TOKEN", "manual-token")
    monkeypatch.setenv("CURRENCY_A2A_AUDIENCE", "ignored")

    assert isinstance(token_provider_from_env(), StaticTokenProvider)


def test_env_provider_uses_the_metadata_server_when_audience_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CURRENCY_A2A_BEARER_TOKEN", raising=False)
    monkeypatch.setenv("CURRENCY_A2A_AUDIENCE", "currencybench-agentcore-worker")

    assert isinstance(token_provider_from_env(), GoogleIdTokenProvider)


def test_env_provider_is_absent_without_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CURRENCY_A2A_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("CURRENCY_A2A_AUDIENCE", raising=False)

    assert token_provider_from_env() is None


def _patch_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Route the provider's AsyncClient through an in-memory transport."""
    original = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
