from datetime import UTC, datetime, timedelta

import httpx
import pytest

from coordinator.aws_identity import (
    SigV4Auth,
    WebIdentityCredentials,
    fetch_google_id_token,
    signer_from_env,
)
from coordinator.errors import AdapterError, FailureKind

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


def _sts_response(expires_in_seconds: int, key_id: str = "ASIAEXAMPLE") -> str:
    expiry = (NOW + timedelta(seconds=expires_in_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"""<?xml version="1.0"?>
<AssumeRoleWithWebIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">
  <AssumeRoleWithWebIdentityResult>
    <Credentials>
      <AccessKeyId>{key_id}</AccessKeyId>
      <SecretAccessKey>secret</SecretAccessKey>
      <SessionToken>session-token</SessionToken>
      <Expiration>{expiry}</Expiration>
    </Credentials>
  </AssumeRoleWithWebIdentityResult>
</AssumeRoleWithWebIdentityResponse>"""


def _patch_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Route every AsyncClient in the module through an in-memory transport."""
    original = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)


async def _static_token(audience: str) -> str:
    return f"id-token-for-{audience}"


def _credentials(handler, monkeypatch, *, clock=None) -> WebIdentityCredentials:
    _patch_transport(monkeypatch, handler)
    return WebIdentityCredentials(
        "arn:aws:iam::123456789012:role/currencybench-coordinator",
        "currencybench-agentcore-worker",
        sts_endpoint="https://sts.test/",
        token_source=_static_token,
        now=clock or (lambda: NOW),
    )


@pytest.mark.asyncio
async def test_id_token_is_requested_with_full_format_and_flavor_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """format=full is what makes Google include the identity detail; without it
    the token is trimmed."""
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["flavor"] = request.headers.get("Metadata-Flavor")
        return httpx.Response(200, text="header.payload.signature\n")

    _patch_transport(monkeypatch, handler)

    token = await fetch_google_id_token("aud-1", metadata_url="http://metadata.test/token")

    assert token == "header.payload.signature"
    assert seen["flavor"] == "Google"
    assert "audience=aud-1" in seen["url"]
    assert "format=full" in seen["url"]


@pytest.mark.asyncio
async def test_metadata_rejection_is_classified_as_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    _patch_transport(monkeypatch, handler)

    with pytest.raises(AdapterError) as excinfo:
        await fetch_google_id_token("aud-1", metadata_url="http://metadata.test/token")
    assert excinfo.value.kind is FailureKind.AUTHENTICATION


@pytest.mark.asyncio
async def test_web_identity_exchange_posts_the_token_and_parses_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(200, text=_sts_response(3600))

    credentials = await _credentials(handler, monkeypatch).credentials()

    assert "Action=AssumeRoleWithWebIdentity" in seen["body"]
    assert "WebIdentityToken=id-token-for-currencybench-agentcore-worker" in seen["body"]
    assert "RoleArn=arn" in seen["body"]
    assert credentials.access_key == "ASIAEXAMPLE"
    assert credentials.token == "session-token"


@pytest.mark.asyncio
async def test_credentials_are_cached_until_they_near_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text=_sts_response(3600, key_id=f"ASIA{calls['n']}"))

    clock = {"now": NOW}
    provider = _credentials(handler, monkeypatch, clock=lambda: clock["now"])

    assert (await provider.credentials()).access_key == "ASIA1"

    clock["now"] = NOW + timedelta(seconds=3000)  # inside the 300 s margin
    assert (await provider.credentials()).access_key == "ASIA1"
    assert calls["n"] == 1

    clock["now"] = NOW + timedelta(seconds=3400)  # past it
    assert (await provider.credentials()).access_key == "ASIA2"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_sts_rejection_is_classified_as_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="<ErrorResponse/>")

    with pytest.raises(AdapterError) as excinfo:
        await _credentials(handler, monkeypatch).credentials()
    assert excinfo.value.kind is FailureKind.AUTHENTICATION


@pytest.mark.asyncio
async def test_malformed_sts_response_is_classified_as_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<AssumeRoleWithWebIdentityResponse/>")

    with pytest.raises(AdapterError) as excinfo:
        await _credentials(handler, monkeypatch).credentials()
    assert excinfo.value.kind is FailureKind.AUTHENTICATION


@pytest.mark.asyncio
async def test_signer_adds_sigv4_headers_to_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_sts_response(3600))

    signer = SigV4Auth(_credentials(handler, monkeypatch), region="us-east-1")
    request = httpx.Request(
        "POST",
        "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/abc",
        json={"jsonrpc": "2.0"},
    )

    flow = signer.async_auth_flow(request)
    signed = await flow.__anext__()
    await flow.aclose()

    assert signed.headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=ASIAEXAMPLE/")
    assert "SignedHeaders=" in signed.headers["Authorization"]
    assert signed.headers["X-Amz-Security-Token"] == "session-token"
    assert "X-Amz-Date" in signed.headers


def test_signer_requires_a_region() -> None:
    with pytest.raises(ValueError):
        SigV4Auth(object(), region="")


def test_web_identity_requires_role_and_audience() -> None:
    with pytest.raises(ValueError):
        WebIdentityCredentials("", "aud")
    with pytest.raises(ValueError):
        WebIdentityCredentials("arn:aws:iam::123456789012:role/r", "")


def test_signer_from_env_builds_a_signer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURRENCY_AWS_ROLE_ARN", "arn:aws:iam::123456789012:role/r")
    monkeypatch.setenv("CURRENCY_A2A_AUDIENCE", "currencybench-agentcore-worker")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    assert isinstance(signer_from_env(), SigV4Auth)


@pytest.mark.parametrize("missing", ["CURRENCY_A2A_AUDIENCE", "AWS_REGION"])
def test_partial_configuration_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    """Half-configured auth must not silently degrade to unsigned requests."""
    monkeypatch.setenv("CURRENCY_AWS_ROLE_ARN", "arn:aws:iam::123456789012:role/r")
    monkeypatch.setenv("CURRENCY_A2A_AUDIENCE", "aud")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(AdapterError) as excinfo:
        signer_from_env()
    assert excinfo.value.kind is FailureKind.AUTHENTICATION


def test_ambient_aws_credentials_are_used_when_no_role_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The workstation path: an operator who assumed the role by hand exports
    credentials, and signing must use them instead of doing nothing."""
    monkeypatch.delenv("CURRENCY_AWS_ROLE_ARN", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ASIALOCAL")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "session")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    assert isinstance(signer_from_env(), SigV4Auth)


def test_role_arn_takes_precedence_over_ambient_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CURRENCY_AWS_ROLE_ARN", "arn:aws:iam::123456789012:role/r")
    monkeypatch.setenv("CURRENCY_A2A_AUDIENCE", "aud")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ASIALOCAL")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    signer = signer_from_env()

    assert isinstance(signer._credentials_source, WebIdentityCredentials)


@pytest.mark.asyncio
async def test_static_credentials_sign_without_any_network_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CURRENCY_AWS_ROLE_ARN", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ASIALOCAL")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)

    signer = signer_from_env()
    request = httpx.Request("POST", "https://bedrock-agentcore.us-east-1.amazonaws.com/x", json={})

    flow = signer.async_auth_flow(request)
    signed = await flow.__anext__()
    await flow.aclose()

    assert "Credential=ASIALOCAL/" in signed.headers["Authorization"]
    assert "X-Amz-Security-Token" not in signed.headers


def test_no_signer_when_nothing_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CURRENCY_AWS_ROLE_ARN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    assert signer_from_env() is None
