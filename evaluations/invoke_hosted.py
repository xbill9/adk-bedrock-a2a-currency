"""Invoke the AgentCore-hosted coordinator runtime.

Usage:
    export AGENTCORE_RUNTIME_ARN="arn:aws:bedrock-agentcore:REGION:ACCOUNT:runtime/NAME"
    python3 -m evaluations.invoke_hosted "Convert 500 EUR to USD and CHF in verified mode."

Authenticates with the standard AWS credential chain (SigV4); the caller needs
bedrock-agentcore:InvokeAgentRuntime on the runtime ARN. The runtime ARN is
printed by `agentcore deploy` (see infra/README.md).
"""

import json
import os
import sys


def extract_result_text(payload: dict) -> list[str]:
    """Return the entrypoint's non-empty result text parts."""
    result = payload.get("result")
    if isinstance(result, str) and result:
        return [result]
    return []


def response_diagnostic(payload: dict) -> dict:
    """Return non-sensitive response metadata for an empty-output failure."""
    return {
        "error": payload.get("error"),
        "detail": payload.get("detail"),
        "keys": sorted(payload.keys()),
    }


def main() -> int:
    runtime_arn = os.environ.get("AGENTCORE_RUNTIME_ARN")
    if not runtime_arn or len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    import boto3

    client = boto3.client("bedrock-agentcore", region_name=os.environ.get("AWS_REGION"))
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        qualifier=os.environ.get("AGENTCORE_QUALIFIER", "DEFAULT"),
        payload=json.dumps({"prompt": sys.argv[1]}).encode(),
    )
    body = response["response"].read().decode("utf-8")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        print(f"Hosted response was not JSON (contentType "
              f"{response.get('contentType')!r}): {body[:500]}", file=sys.stderr)
        return 1
    texts = extract_result_text(payload)
    if not texts:
        print(
            f"Hosted response contained no result text: {response_diagnostic(payload)}",
            file=sys.stderr,
        )
        return 1
    for output_text in texts:
        print(output_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
