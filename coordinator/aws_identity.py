"""Keyless AWS credentials for the Cloud Run coordinator, via GCP → AWS WIF.

The coordinator holds no AWS access keys. It authenticates to Bedrock AgentCore
Runtime — which uses the default ``AWS_IAM`` inbound authorizer — like this:

1. Cloud Run's metadata server mints a Google-issued OIDC ID token for the
   coordinator's service account, with ``audience`` set to
   ``CURRENCY_A2A_AUDIENCE``.
2. That token is exchanged for temporary AWS credentials via STS
   ``AssumeRoleWithWebIdentity``. The role's trust policy conditions on both the
   audience and the service account's numeric ``sub``, so AWS itself decides
   whether this caller is allowed to assume it.
3. Requests to the runtime are SigV4-signed with those credentials.

The trust decision therefore lives in an IAM trust policy rather than a claim
string match, which is what gives IAM policy granularity, CloudTrail attribution
under a real principal, and revocation by editing the policy.

``AssumeRoleWithWebIdentity`` is one of the few AWS APIs that takes no
credentials — that is how web-identity federation bootstraps — so the exchange
is a plain HTTP POST here and needs no AWS SDK. ``botocore`` is used only for
SigV4 signing, which is not worth reimplementing.
"""

import logging
import os
from datetime import UTC, datetime
from xml.etree import ElementTree

import httpx

from coordinator.errors import AdapterError, FailureKind

logger = logging.getLogger(__name__)

METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/identity"
)
METADATA_FLAVOR_HEADER = {"Metadata-Flavor": "Google"}

DEFAULT_STS_ENDPOINT = "https://sts.amazonaws.com/"
STS_API_VERSION = "2011-06-15"
STS_NAMESPACE = {"sts": "https://sts.amazonaws.com/doc/2011-06-15/"}

AGENTCORE_SERVICE = "bedrock-agentcore"

# Refresh early so credentials cannot expire in flight during a slow call.
_REFRESH_MARGIN_SECONDS = 300


async def fetch_google_id_token(
    audience: str, *, metadata_url: str = METADATA_TOKEN_URL, timeout_s: float = 10.0
) -> str:
    """Mint an OIDC ID token for this workload's service account.

    ``format=full`` is required: without it Google omits the ``email`` claim and
    trims the token, and the full form is what carries the identity detail the
    IAM trust policy and CloudTrail rely on.
    """
    params = {"audience": audience, "format": "full"}
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.get(
                metadata_url, params=params, headers=METADATA_FLAVOR_HEADER
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
            FailureKind.AUTHENTICATION, f"cannot reach the GCP metadata server: {exc}"
        ) from exc
    token = response.text.strip()
    if not token:
        raise AdapterError(
            FailureKind.AUTHENTICATION, "metadata server returned an empty ID token"
        )
    return token


class WebIdentityCredentials:
    """Exchanges a Google ID token for temporary AWS credentials, with caching."""

    def __init__(
        self,
        role_arn: str,
        audience: str,
        *,
        session_name: str = "currency-coordinator",
        sts_endpoint: str = DEFAULT_STS_ENDPOINT,
        timeout_s: float = 15.0,
        token_source=fetch_google_id_token,
        now=lambda: datetime.now(UTC),
    ) -> None:
        if not role_arn:
            raise ValueError("role_arn is required to assume an AWS role")
        if not audience:
            raise ValueError("audience is required to mint a Google ID token")
        self._role_arn = role_arn
        self._audience = audience
        self._session_name = session_name
        self._sts_endpoint = sts_endpoint
        self._timeout_s = timeout_s
        self._token_source = token_source
        self._now = now
        self._cached = None
        self._expires_at = None

    async def credentials(self):
        """Return botocore Credentials, refreshing shortly before expiry."""
        if self._cached is not None and self._expires_at is not None:
            remaining = (self._expires_at - self._now()).total_seconds()
            if remaining > _REFRESH_MARGIN_SECONDS:
                return self._cached
        self._cached, self._expires_at = await self._assume_role()
        return self._cached

    async def _assume_role(self):
        from botocore.credentials import Credentials

        id_token = await self._token_source(self._audience)
        payload = {
            "Action": "AssumeRoleWithWebIdentity",
            "Version": STS_API_VERSION,
            "RoleArn": self._role_arn,
            "RoleSessionName": self._session_name,
            "WebIdentityToken": id_token,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(self._sts_endpoint, data=payload)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # STS puts the actionable reason in the body -- "Incorrect token
            # audience", "Not authorized to perform sts:AssumeRoleWithWebIdentity",
            # and so on. Without it the status code alone cannot distinguish a
            # trust-policy mismatch from a malformed token, so surface it. The
            # body carries an error code and message, never the ID token.
            detail = " ".join(exc.response.text.split())[:400]
            # Log server-side as well: the AdapterError message travels back
            # through the model, which paraphrases it and drops the detail.
            logger.error("STS AssumeRoleWithWebIdentity failed: %s", detail)
            raise AdapterError(
                FailureKind.AUTHENTICATION,
                f"STS refused AssumeRoleWithWebIdentity ({exc.response.status_code}); "
                "check the role trust policy's audience and sub conditions. "
                f"STS said: {detail}",
            ) from exc
        except (httpx.TransportError, OSError) as exc:
            raise AdapterError(
                FailureKind.AUTHENTICATION, f"cannot reach AWS STS: {exc}"
            ) from exc

        try:
            root = ElementTree.fromstring(response.text)
            node = root.find(".//sts:Credentials", STS_NAMESPACE)
            if node is None:
                raise ValueError("no Credentials element in the STS response")
            access_key = node.findtext("sts:AccessKeyId", namespaces=STS_NAMESPACE)
            secret_key = node.findtext("sts:SecretAccessKey", namespaces=STS_NAMESPACE)
            token = node.findtext("sts:SessionToken", namespaces=STS_NAMESPACE)
            expiration = node.findtext("sts:Expiration", namespaces=STS_NAMESPACE)
            if not (access_key and secret_key and token and expiration):
                raise ValueError("incomplete Credentials element in the STS response")
            # fromisoformat handles the trailing "Z" natively on 3.11+.
            expires_at = datetime.fromisoformat(expiration)
        except (ElementTree.ParseError, ValueError) as exc:
            raise AdapterError(
                FailureKind.AUTHENTICATION, f"malformed STS response: {exc}"
            ) from exc

        return Credentials(access_key, secret_key, token), expires_at


class StaticCredentials:
    """Wraps already-resolved credentials, e.g. the ambient AWS environment.

    This is the workstation path: an operator who has assumed the role by hand
    exports AWS_ACCESS_KEY_ID and friends, and signing uses them directly. The
    deployed coordinator never takes this path -- it federates per credential
    lifetime and holds nothing long-lived.
    """

    def __init__(self, access_key: str, secret_key: str, token: str | None = None) -> None:
        self._access_key = access_key
        self._secret_key = secret_key
        self._token = token

    async def credentials(self):
        from botocore.credentials import Credentials

        return Credentials(self._access_key, self._secret_key, self._token)


class SigV4Auth(httpx.Auth):
    """Signs each outgoing request with SigV4 using freshly assumed credentials.

    a2a-sdk builds its own requests but accepts a caller-supplied
    ``httpx.AsyncClient``, so attaching this as the client's ``auth`` signs the
    agent-card fetch and every JSON-RPC call without touching the SDK.
    """

    # SigV4 hashes the body, so httpx must materialise it before signing.
    requires_request_body = True

    def __init__(self, credentials_source, *, region: str, service: str = AGENTCORE_SERVICE) -> None:
        if not region:
            raise ValueError("region is required to sign SigV4 requests")
        self._credentials_source = credentials_source
        self._region = region
        self._service = service

    async def async_auth_flow(self, request):
        from botocore.auth import SigV4Auth as BotoSigV4Auth
        from botocore.awsrequest import AWSRequest

        credentials = await self._credentials_source.credentials()
        aws_request = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            # Host is recomputed by the signer; passing httpx's own hop-by-hop
            # headers through would only invalidate the signature.
            headers={
                key: value
                for key, value in request.headers.items()
                if key.lower() in {"content-type", "accept"}
            },
        )
        BotoSigV4Auth(credentials, self._service, self._region).add_auth(aws_request)
        for key, value in aws_request.headers.items():
            request.headers[key] = value
        yield request


def signer_from_env() -> SigV4Auth | None:
    """Build the request signer implied by the environment, or None for no auth.

    Three cases, in order:

    1. ``CURRENCY_AWS_ROLE_ARN`` set — federate from the Google identity. This is
       the deployed Cloud Run path.
    2. Ambient AWS credentials in the environment — sign with them directly. This
       is the workstation path (see tier 3 in ``docs/E2E_TESTING.md``).
    3. Neither — no signing at all, which keeps a local unauthenticated worker
       and the fixture adapters usable without AWS.
    """
    region = os.getenv("AWS_REGION", "").strip()
    role_arn = os.getenv("CURRENCY_AWS_ROLE_ARN", "").strip()

    if role_arn:
        audience = os.getenv("CURRENCY_A2A_AUDIENCE", "").strip()
        if not audience:
            raise AdapterError(
                FailureKind.AUTHENTICATION,
                "CURRENCY_A2A_AUDIENCE is required when CURRENCY_AWS_ROLE_ARN is set",
            )
        if not region:
            raise AdapterError(
                FailureKind.AUTHENTICATION,
                "AWS_REGION is required when CURRENCY_AWS_ROLE_ARN is set",
            )
        credentials = WebIdentityCredentials(
            role_arn,
            audience,
            sts_endpoint=os.getenv("CURRENCY_AWS_STS_ENDPOINT", DEFAULT_STS_ENDPOINT),
        )
        return SigV4Auth(credentials, region=region)

    access_key = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
    if access_key and secret_key:
        if not region:
            raise AdapterError(
                FailureKind.AUTHENTICATION,
                "AWS_REGION is required to sign with ambient AWS credentials",
            )
        static = StaticCredentials(
            access_key, secret_key, os.getenv("AWS_SESSION_TOKEN", "").strip() or None
        )
        return SigV4Auth(static, region=region)

    return None
