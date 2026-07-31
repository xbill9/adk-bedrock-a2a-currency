"""Invoke the Cloud Run-hosted ADK coordinator over A2A.

Usage:
    export CURRENCY_COORDINATOR_ENDPOINT="https://currency-adk-coordinator-....run.app"
    python3 -m evaluations.invoke_hosted "Convert 500 EUR to USD and CHF in verified mode."

The coordinator is the master in this topology, so the benchmark drives it
directly and it fans out to MCP and to the AgentCore worker on AWS.

Authentication depends on how the Cloud Run service is deployed:

- ``--allow-unauthenticated`` (the default in ``infra/deploy_live.sh``): no
  credentials needed here.
- private service: set ``CURRENCY_COORDINATOR_TOKEN`` to an identity token for
  the service, e.g.
  ``export CURRENCY_COORDINATOR_TOKEN=$(gcloud auth print-identity-token)``.

Note this is the *client* half of the same A2A v1.0 protocol the coordinator
uses to reach AWS; it reuses ``coordinator.a2a_remote`` transport conventions
rather than boto3, because the entrypoint is no longer an AWS runtime.
"""

import asyncio
import os
import sys

import httpx


def auth_headers(token: str | None) -> dict[str, str]:
    """Bearer header for a private Cloud Run service, or nothing at all."""
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


async def send(endpoint: str, prompt: str, token: str | None, timeout_s: float) -> str:
    """Resolve the coordinator's agent card and send one text message."""
    from a2a.client import A2ACardResolver, ClientConfig, create_client
    from a2a.helpers import get_message_text, get_text_parts, new_text_message
    from a2a.types import Role, SendMessageRequest

    headers = auth_headers(token)
    # local_address pins the socket to IPv4; IPv6 to some hosts hangs in some
    # sandboxes (same workaround as coordinator/a2a_remote.py).
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
    async with httpx.AsyncClient(
        timeout=timeout_s, transport=transport, headers=headers
    ) as httpx_client:
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=endpoint)
        card = await resolver.get_agent_card()
        # ADK's to_a2a() advertises the container's bind address on the card;
        # route to the endpoint we were given instead.
        for interface in card.supported_interfaces:
            interface.url = endpoint
        client = await create_client(
            agent=card,
            client_config=ClientConfig(streaming=False, httpx_client=httpx_client),
        )
        try:
            request = SendMessageRequest(message=new_text_message(prompt, role=Role.ROLE_USER))
            texts: list[str] = []
            async for chunk in client.send_message(request):
                if chunk.HasField("message"):
                    texts.append(get_message_text(chunk.message))
                elif chunk.HasField("task"):
                    for artifact in chunk.task.artifacts:
                        texts.append("\n".join(get_text_parts(artifact.parts)))
            return "\n".join(text for text in texts if text)
        finally:
            await client.close()


def main() -> int:
    endpoint = os.environ.get("CURRENCY_COORDINATOR_ENDPOINT", "").strip()
    if not endpoint or len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    timeout_s = float(os.environ.get("CURRENCY_TIMEOUT_SECONDS", "120"))
    token = os.environ.get("CURRENCY_COORDINATOR_TOKEN", "").strip() or None
    try:
        output_text = asyncio.run(send(endpoint, sys.argv[1], token, timeout_s))
    except Exception as exc:  # noqa: BLE001 - operator tool reports any failure verbatim
        print(f"Coordinator invocation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if not output_text:
        print("Coordinator returned no text parts", file=sys.stderr)
        return 1
    print(output_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
