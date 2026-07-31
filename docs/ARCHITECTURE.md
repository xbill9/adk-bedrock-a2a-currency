# Architecture and trust boundaries

## Components

1. **Client** submits a structured conversion request.
2. **Cloud Run-hosted Google ADK master** uses Gemini to choose the benchmark
   path, call the AWS AgentCore worker over A2A, and produce the final
   explanation.
3. **MCP server** obtains a timestamped exchange rate and performs deterministic
   decimal arithmetic. The master spawns it over stdio per request.
4. **Bedrock AgentCore worker** independently calculates or verifies the
   conversion and is invoked by the ADK master through A2A v1.0. It runs a
   Strands agent on a Bedrock model.
5. **Comparator** checks identity fields and relative numeric difference without
   asking a model to judge arithmetic.
6. **Evaluation runner** captures correctness, latency, cost, failures, versions,
   and trace correlation.

The domain core in `coordinator/` is framework-independent: `CurrencyCoordinator`
takes two duck-typed adapters, so which cloud hosts the master is a deployment
decision, not a code one. Reversing the direction changed the hosting and the
authentication story, not the comparison logic.

## Trust boundaries

- The client input is untrusted.
- MCP tool descriptions and outputs are untrusted model context.
- A2A cards and remote-agent messages are untrusted remote content.
- Authentication proves an identity, not the correctness of returned data.
- Model prose is never the system of record for amounts or rates.
- Secrets must remain in GCP Secret Manager and AWS IAM/execution roles; the
  coordinator container carries no long-lived keys.

## Cross-cloud authentication (GCP master → AWS worker)

The coordinator holds **no long-lived AWS credentials**. AgentCore Runtime keeps
its default `AWS_IAM` authorizer, and the coordinator federates into AWS from
its Google identity (`coordinator/aws_identity.py`):

1. Cloud Run's metadata server mints a Google-issued OIDC ID token for the
   coordinator's service account, with `audience` set to
   `CURRENCY_A2A_AUDIENCE`. `format=full` is required — without it Google omits
   the `email` claim and trims the token.
2. That token is exchanged for temporary AWS credentials via STS
   `AssumeRoleWithWebIdentity`. This API takes no credentials of its own — that
   is how web-identity federation bootstraps — so the exchange needs no AWS SDK.
3. Requests to the runtime are SigV4-signed with those credentials. The signer
   is attached to the shared `httpx` client, so it covers both the agent-card
   fetch and every JSON-RPC call a2a-sdk builds internally.

**Audience alone is not authorization.** Any Google principal can request an ID
token for an arbitrary audience string, so an audience match only proves that
*some* Google identity minted the token. The role's trust policy therefore
conditions on both `accounts.google.com:aud` and `accounts.google.com:sub` — the
service account's immutable numeric ID, not its email, because an email is a
name that can be freed and re-bound if the account is deleted and recreated.

The trust decision lives in an IAM trust policy rather than a claim-string
matcher in application config. That is what buys IAM policy granularity,
CloudTrail attribution under a real principal, and revocation by editing the
policy. `infra/deploy_live.sh` also scopes an `InvokeAgentRuntime` policy to the
single deployed runtime ARN rather than `*`.

`botocore` is a dependency for SigV4 signing only, imported lazily so the domain
core stays importable without it. An earlier revision used a `CUSTOM_JWT`
authorizer with a bearer token; it was replaced because a claim matcher in
config is weaker and easier to defeat by editing than an IAM trust policy.

## A2A protocol version constraint

Both ends must speak the same A2A wire version, and this is the sharpest
constraint in the lab:

- `google-adk[a2a]==2.5.0` and the `a2a-sdk` 1.x client speak **A2A v1.0**.
- `strands-agents[a2a]` pins `a2a-sdk<0.4`, i.e. **A2A v0.3** wire methods, even
  at the latest release. A v1.0 client cannot call a v0.3 server.

The worker therefore uses `strands-agents` for the agent loop only and builds
its A2A server from `a2a-sdk[http-server]` directly
(`app/CurrencyWorker/main.py`). This is the same version split that forced the
A2UI extension out of the ADK agent when it was the worker, encountered from the
opposite side. `tests/test_deployment_config.py` asserts the lockfile resolves
`a2a-sdk` 1.x so a dependency bump cannot silently reintroduce the mismatch.

## Failure policy

- If MCP fails and A2A succeeds, return a clearly labeled unverified remote result.
- If A2A fails and MCP succeeds, return the tool result with verification missing.
- If both succeed but disagree beyond tolerance, return both and warn; never
  silently choose the model's preferred answer.
- If both fail, return a typed failure without fabricating a rate.
- If the rate is stale, show its timestamp and flag it.
- A failed token mint or STS exchange is reported as an `authentication`
  failure, never relabeled as a protocol error, so the benchmark attributes it
  correctly.
- Half-configured auth fails loudly. If a role ARN is set without an audience or
  region, the coordinator raises rather than silently sending unsigned requests.

## Platform constraints to test and document

- AgentCore Runtime's A2A protocol proxies JSON-RPC to the container on port
  9000 and expects the agent card at `/.well-known/agent-card.json`. The A2A hop
  in this lab is now inbound to AWS: the worker is an A2A *server*.
- Hosted coordinator deployments set `CURRENCY_REQUIRE_AWS_AGENTCORE=1`; A2A and
  verified requests fail closed if `CURRENCY_A2A_ENDPOINT` is missing. This
  prevents a local fixture from masquerading as successful cross-cloud
  delegation.
- Cross-cloud egress: the Cloud Run container must reach AgentCore, AWS STS, the
  GCP metadata server, and the Frankfurter API.
- Agent cards advertise the server's own bind address; the client rewrites
  `supported_interfaces[].url` to the configured endpoint before dispatching.
- SDK package names, IAM permissions, and deployment shapes may change; pin
  versions once an end-to-end deployment is verified.
