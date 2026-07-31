"""Google-issued OIDC identity tokens for calling the AWS AgentCore worker.

The Cloud Run coordinator authenticates to Bedrock AgentCore Runtime with a
Google ID token rather than AWS credentials: AgentCore is configured with a
CUSTOM_JWT authorizer pointing at Google's OIDC discovery document, so it
validates the token's signature, ``aud``, and (optionally) the service-account
``email`` claim itself. Nothing long-lived is ever stored in the container,
which is what ``docs/ARCHITECTURE.md`` requires of the coordinator.

Tokens come from the GCE/Cloud Run metadata server, which mints them for the
runtime service account on demand. There is no dependency on
``google-auth`` here so the coordinator core stays import-light and testable;
the metadata contract is a single documented HTTP GET.
"""

import os
import time
from typing import Protocol

import httpx

from coordinator.errors import AdapterError, FailureKind

METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/identity"
)
METADATA_FLAVOR_HEADER = {"Metadata-Flavor": "Google"}

# Google ID tokens last an hour. Refresh early so a token cannot expire in
# flight during a slow cross-cloud call.
_REFRESH_MARGIN_SECONDS = 300


class TokenProvider(Protocol):
    async def token(self) -> str: ...


class StaticTokenProvider:
    """Returns a pre-issued token; used by tests and for local experiments."""

    def __init__(self, value: str) -> None:
        self._value = value

    async def token(self) -> str:
        return self._value


class GoogleIdTokenProvider:
    """Fetches and caches an ID token for ``audience`` from the metadata server.

    ``audience`` must match one of the ``allowedAudience`` entries configured on
    the AgentCore runtime's JWT authorizer, otherwise AgentCore rejects the call
    with 403.
    """

    def __init__(
        self,
        audience: str,
        *,
        metadata_url: str = METADATA_TOKEN_URL,
        timeout_s: float = 10.0,
        now = time.monotonic,
    ) -> None:
        if not audience:
            raise ValueError("audience is required to mint a Google ID token")
        self._audience = audience
        self._metadata_url = metadata_url
        self._timeout_s = timeout_s
        self._now = now
        self._cached: str | None = None
        self._expires_at: float = 0.0

    async def token(self) -> str:
        if self._cached and self._now() < self._expires_at:
            return self._cached
        raw = await self._fetch()
        self._cached = raw
        # The metadata server does not return an expiry alongside the token, so
        # cache for a conservative fraction of the standard 1 h lifetime.
        self._expires_at = self._now() + (3600 - _REFRESH_MARGIN_SECONDS)
        return raw

    async def _fetch(self) -> str:
        params = {"audience": self._audience, "format": "full"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.get(
                    self._metadata_url, params=params, headers=METADATA_FLAVOR_HEADER
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AdapterError(
                FailureKind.AUTHENTICATION,
                f"metadata server refused an ID token ({exc.response.status_code}); "
                "is the service running on Cloud Run with a service account?",
            ) from exc
        except (httpx.TransportError, OSError) as exc:
            raise AdapterError(
                FailureKind.AUTHENTICATION,
                f"cannot reach the GCP metadata server: {exc}",
            ) from exc
        token = response.text.strip()
        if not token:
            raise AdapterError(
                FailureKind.AUTHENTICATION, "metadata server returned an empty ID token"
            )
        return token


def token_provider_from_env(audience: str | None = None) -> TokenProvider | None:
    """Build the provider implied by the environment, or None for no auth.

    ``CURRENCY_A2A_BEARER_TOKEN`` short-circuits the metadata server so the loop
    can be exercised from a laptop with a manually minted token.
    """
    static = os.getenv("CURRENCY_A2A_BEARER_TOKEN", "").strip()
    if static:
        return StaticTokenProvider(static)
    resolved = (audience or os.getenv("CURRENCY_A2A_AUDIENCE", "")).strip()
    if not resolved:
        return None
    return GoogleIdTokenProvider(resolved)
