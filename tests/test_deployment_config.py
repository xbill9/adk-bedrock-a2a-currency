import json
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]

GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"


def _runtime() -> dict:
    config = json.loads((ROOT / "agentcore/agentcore.json").read_text())
    return config["runtimes"][0]


def _runtime_env() -> dict[str, str]:
    return {item["name"]: item["value"] for item in _runtime()["envVars"]}


def test_agentcore_worker_serves_a2a_and_caps_bedrock_output() -> None:
    runtime = _runtime()
    env = _runtime_env()

    assert runtime["protocol"] == "A2A"
    assert runtime["codeLocation"] == "app/CurrencyWorker/"
    assert env["BEDROCK_MODEL_ID"].startswith(("us.", "global."))
    assert env["BEDROCK_MAX_TOKENS"] == "1024"
    # The worker is the remote agent now; it must not carry an outbound endpoint.
    assert "CURRENCY_A2A_ENDPOINT" not in env


def test_agentcore_worker_requires_google_issued_tokens() -> None:
    runtime = _runtime()

    assert runtime["authorizerType"] == "CUSTOM_JWT"
    authorizer = runtime["authorizerConfiguration"]["customJwtAuthorizer"]
    assert authorizer["discoveryUrl"] == GOOGLE_DISCOVERY_URL
    assert authorizer["allowedAudience"]


def test_agentcore_authorizer_pins_a_caller_identity_claim() -> None:
    """Audience alone only proves 'some Google principal' minted the token.

    Any Google service account can request an ID token for an arbitrary
    audience string, so authorization depends on matching the coordinator's
    own identity claim.
    """
    authorizer = _runtime()["authorizerConfiguration"]["customJwtAuthorizer"]
    claims = authorizer["customClaims"]

    assert len(claims) == 1
    claim = claims[0]
    assert claim["inboundTokenClaimName"] == "email"
    assert claim["authorizingClaimMatchValue"]["claimMatchOperator"] == "EQUALS"
    assert claim["authorizingClaimMatchValue"]["claimMatchValue"]["matchValueString"]


def _assert_bundle_matches(bundle_root: str, package_file: str) -> None:
    """Compare a synced copy with its source, skipping if it was never synced.

    The copies are gitignored build artifacts, so a fresh clone has none until
    infra/sync_app.sh or infra/sync_adk.sh runs. Skipping keeps the suite green
    on a clean checkout while still catching a *stale* bundle before deploy.
    """
    bundled = ROOT / bundle_root / package_file
    if not bundled.exists():
        pytest.skip(f"{bundle_root}/{package_file} not synced; run infra/sync_*.sh")

    assert bundled.read_bytes() == (ROOT / package_file).read_bytes(), package_file


def test_agentcore_bundle_contains_current_hosted_adapter() -> None:
    _assert_bundle_matches("app/CurrencyWorker", "coordinator/hosted_tool.py")


def test_adk_bundle_contains_current_hosted_adapter() -> None:
    """The Cloud Run master runs the benchmark tool, so its copy must be fresh."""
    for package_file in ("coordinator/hosted_tool.py", "mcp_server/server.py"):
        _assert_bundle_matches("adk_agent", package_file)


def test_cloud_run_serves_the_adk_master_and_injects_no_secret() -> None:
    start_script = (ROOT / "adk_agent/start.sh").read_text()
    dockerfile = (ROOT / "adk_agent/Dockerfile").read_text()

    assert "uvicorn agent:a2a_app" in start_script
    # The MCP rate server is spawned over stdio per request, not colocated.
    assert "mcp_server.py &" not in start_script
    assert "COPY coordinator/" in dockerfile
    assert "COPY mcp_server/" in dockerfile
    assert "chmod +x start.sh" in dockerfile
    assert "GOOGLE_API_KEY=" not in dockerfile


def test_adk_master_prompt_parses_natural_language_targets() -> None:
    entrypoint = (ROOT / "adk_agent/agent.py").read_text()

    assert "target_currencies=['EUR']" in entrypoint
    assert "Never ask the user to confirm information already present" in entrypoint


def test_worker_avoids_the_incompatible_strands_a2a_extra() -> None:
    """strands-agents[a2a] pins a2a-sdk<0.4 (A2A v0.3), which a v1.0 client
    cannot call. The worker must build its server from a2a-sdk directly."""
    pyproject = tomllib.loads((ROOT / "app/CurrencyWorker/pyproject.toml").read_text())
    dependencies = pyproject["project"]["dependencies"]

    assert not any(dep.startswith("strands-agents[a2a]") for dep in dependencies)
    assert any(dep.startswith("strands-agents") for dep in dependencies)
    assert any(dep.startswith("a2a-sdk[http-server]") for dep in dependencies)


def test_worker_lockfile_resolves_a2a_v1_wire_protocol() -> None:
    """The lock is the real guarantee both ends speak the same A2A version."""
    lock = tomllib.loads((ROOT / "app/CurrencyWorker/uv.lock").read_text())
    versions = {pkg["name"]: pkg["version"] for pkg in lock["package"]}

    assert versions["a2a-sdk"].startswith("1.")
