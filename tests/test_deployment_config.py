import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _runtime_env() -> dict[str, str]:
    config = json.loads((ROOT / "agentcore/agentcore.json").read_text())
    return {
        item["name"]: item["value"]
        for item in config["runtimes"][0]["envVars"]
    }


def test_agentcore_requires_gcp_worker_and_caps_bedrock_output() -> None:
    env = _runtime_env()

    assert env["CURRENCY_REQUIRE_GCP_ADK"] == "1"
    assert env["CURRENCY_A2A_ENDPOINT"].startswith("https://")
    assert env["BEDROCK_MODEL_ID"].startswith(("us.", "global."))
    assert env["BEDROCK_MAX_TOKENS"] == "1024"


def test_agentcore_bundle_contains_current_hosted_adapter() -> None:
    source = ROOT / "coordinator/hosted_tool.py"
    bundled = ROOT / "app/CurrencyCoordinator/coordinator/hosted_tool.py"

    assert bundled.read_bytes() == source.read_bytes()


def test_cloud_run_starts_mcp_before_adk_and_injects_no_secret() -> None:
    start_script = (ROOT / "adk_agent/start.sh").read_text()
    dockerfile = (ROOT / "adk_agent/Dockerfile").read_text()

    assert "python mcp_server.py &" in start_script
    assert "uvicorn agent:a2a_app" in start_script
    assert start_script.index("python mcp_server.py &") < start_script.index(
        "uvicorn agent:a2a_app"
    )
    assert "chmod +x start.sh" in dockerfile
    assert "GOOGLE_API_KEY=" not in dockerfile


def test_bedrock_master_prompt_parses_natural_language_targets() -> None:
    entrypoint = (ROOT / "app/CurrencyCoordinator/main.py").read_text()

    assert "target_currencies=['EUR']" in entrypoint
    assert "Never ask the user to confirm information already present" in entrypoint
