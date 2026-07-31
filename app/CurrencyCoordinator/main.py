"""AgentCore Runtime entrypoint: Bedrock master for the currency benchmark.

The `coordinator/` and `mcp_server/` packages in this directory are copies of
the repo-root packages, synced by `infra/sync_app.sh` before deploy so the
CodeZip bundle is self-contained. Edit the root packages, not the copies.
"""

from collections import OrderedDict

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from model.load import load_model
from strands import Agent, tool
from strands.agent.conversation_manager.null_conversation_manager import NullConversationManager

from coordinator.hosted_tool import run_currency_benchmark

app = BedrockAgentCoreApp()
log = app.logger

SYSTEM_PROMPT = (
    "You are the Amazon Bedrock master agent for a currency interoperability "
    "benchmark, not a general chatbot. The Google ADK agent is your remote worker "
    "and is reached by the benchmark tool over A2A. "
    "For every conversion request, call run_currency_benchmark. Never calculate or "
    "verify arithmetic yourself. Determine whether results are live only from each "
    "returned source field: sources containing 'deterministic-fixture' or "
    "'hosted-local' are non-live; do not label other sources as fixtures. Preserve "
    "the returned amounts, rates, timestamps, failure labels, and warnings exactly. "
    "Parse 'Convert 100 USD to EUR' as amount='100', source_currency='USD', and "
    "target_currencies=['EUR']; every currency listed after 'to' is a target. "
    "Never ask the user to confirm information already present in the request. "
    "Ask for amount, source currency, or target currencies only when truly missing."
)

tools = [tool(run_currency_benchmark)]


# Reuses one Agent per session_id so each session keeps its own in-process
# conversation history (best-effort; resets on cold start). Bounded LRU so one
# process serving many sessions cannot leak history between them or grow
# without limit.
def agent_factory():
    cache = OrderedDict()

    def get_or_create_agent(session_id):
        if session_id in cache:
            cache.move_to_end(session_id)
            return cache[session_id]
        if len(cache) >= 128:
            cache.popitem(last=False)
        cache[session_id] = Agent(
            model=load_model(),
            system_prompt=SYSTEM_PROMPT,
            tools=tools,
            conversation_manager=NullConversationManager(),
        )
        return cache[session_id]

    return get_or_create_agent


get_or_create_agent = agent_factory()


def _extract_prompt(payload: dict):
    """Accept harness-style messages[] or plain prompt string payloads."""
    if "messages" in payload:
        return payload["messages"]
    return payload.get("prompt", "")


@app.entrypoint
async def invoke(payload, context):
    log.info("Invoking Bedrock master currency agent")
    session_id = getattr(context, "session_id", "default-session")
    agent = get_or_create_agent(session_id)
    prompt = _extract_prompt(payload)
    # The benchmark tool is async, so the agent must run on the async path.
    result = await agent.invoke_async(prompt)
    return {"result": str(result)}


if __name__ == "__main__":
    app.run()
