# Architecture and trust boundaries

## Components

1. **Client** submits a structured conversion request.
2. **AgentCore-hosted Bedrock master** uses Strands Agents on an Amazon
   Bedrock model to choose the benchmark path, call the GCP ADK worker over
   A2A, and produce the final explanation.
3. **MCP server** obtains a timestamped exchange rate and performs deterministic
   decimal arithmetic.
4. **GCP Google ADK worker** independently calculates or verifies the
   conversion and is invoked by the Bedrock master through A2A v1.0.
5. **Comparator** checks identity fields and relative numeric difference without
   asking a model to judge arithmetic.
6. **Evaluation runner** captures correctness, latency, cost, failures, versions,
   and trace correlation.

## Trust boundaries

- The client input is untrusted.
- MCP tool descriptions and outputs are untrusted model context.
- A2A cards and remote-agent messages are untrusted remote content.
- Authentication proves an identity, not the correctness of returned data.
- Model prose is never the system of record for amounts or rates.
- Secrets must remain in AWS IAM/execution roles and GCP Secret Manager;
  the coordinator container carries no long-lived keys.

## Failure policy

- If MCP fails and A2A succeeds, return a clearly labeled unverified remote result.
- If A2A fails and MCP succeeds, return the tool result with verification missing.
- If both succeed but disagree beyond tolerance, return both and warn; never
  silently choose the model's preferred answer.
- If both fail, return a typed failure without fabricating a rate.
- If the rate is stale, show its timestamp and flag it.

## Platform constraints to test and document

- AgentCore Runtime invokes the coordinator through its own HTTP contract
  (`/invocations`), so the A2A hop in this lab is outbound only: the
  coordinator is an A2A client of the ADK agent.
- Hosted deployments set `CURRENCY_REQUIRE_GCP_ADK=1`; A2A and verified
  requests fail closed if `CURRENCY_A2A_ENDPOINT` is missing. This prevents a
  local fixture from masquerading as successful cross-cloud delegation.
- Cross-cloud egress: the AgentCore container must reach Cloud Run and the
  Frankfurter API over the public internet.
- SDK package names, IAM permissions, and deployment shapes may change;
  pin versions once an end-to-end deployment is verified.
