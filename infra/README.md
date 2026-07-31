# Cross-cloud deployment (GCP master → AWS worker)

Deploy the A2A worker to Amazon Bedrock AgentCore Runtime with the AgentCore
CLI (`npm install -g @aws/agentcore`, Node 20+, CDK-based), and the ADK master
to Cloud Run. The worker uses the AgentCore execution role; the master carries
no AWS credentials at all. Do not commit API keys.

`infra/deploy_live.sh` drives both halves in dependency order. Run it twice:
the first pass creates the coordinator's service account and deploys the
worker, then stops and asks for `AGENTCORE_A2A_ENDPOINT` (the CLI's output
format is not stable enough to parse blindly); the second pass deploys the
coordinator against that endpoint.

Provision only the MVP resources:

- AgentCore Runtime hosting the Strands A2A worker (`main.py`);
- a `CUSTOM_JWT` authorizer trusting Google's OIDC discovery document;
- Bedrock model access for the chosen inference profile;
- the CLI-created IAM execution role and CloudWatch log group;
- CDK bootstrap assets (S3 staging for CodeZip builds);
- a GCP service account for the coordinator plus the Gemini key secret.

Candidate deployment versions researched on 2026-07-28 (re-pin after the
first verified end-to-end deployment):

- AgentCore CLI: `@aws/agentcore` (npm, current)
- Python runtime SDK: `bedrock-agentcore` `1.18.1`
- Strands Agents: `1.50.2`
- A2A SDK: `1.1.2` (validated live against google-adk 2.5.0 on 2026-07-27)
- Bedrock model: `us.amazon.nova-micro-v1:0` — the cheapest Bedrock model
  with tool calling; no use-case form or agreement needed, unlike Anthropic
  models. Override with `BEDROCK_MODEL_ID` (inference-profile IDs only;
  bare model IDs fail with an on-demand-throughput 400 on newer models)
- Region: the active AWS profile's configured region, or `AWS_REGION`

Note: the older pip-based starter-toolkit flow (`agentcore configure` /
`agentcore launch` from `bedrock-agentcore-starter-toolkit`) was deprecated in
June 2026; use the npm CLI.

## Project layout (already scaffolded)

The AgentCore project was created on 2026-07-28 with
`agentcore create --framework Strands --protocol HTTP --model-provider Bedrock
--memory none` and relocated into the repo root. It was switched to
`"protocol": "A2A"` on 2026-07-30 when the topology was reversed:

- `agentcore/agentcore.json` — project config, including the runtime `envVars`
  and the `CUSTOM_JWT` authorizer (committed);
- `agentcore/aws-targets.json` — account-specific deployment target generated
  locally by `infra/configure_aws_target.sh` and excluded from Git;
- `agentcore/.env.local`, `agentcore/.cli/` — local state (not committed);
- `app/CurrencyWorker/` — the deployable app: `main.py` (a2a-sdk 1.x server
  wrapping a Strands agent), `model/load.py`, its own `pyproject.toml`/
  `uv.lock`, plus copies of `coordinator/` and `mcp_server/` synced by
  `infra/sync_app.sh` (the copies are build artifacts; edit the repo-root
  packages);
- `adk_agent/` — the Cloud Run master, with copies synced by
  `infra/sync_adk.sh`.

## Deploy and smoke-test

```bash
export GCP_PROJECT=my-project
./infra/configure_aws_target.sh       # uses the active AWS profile/account
./infra/deploy_live.sh                # SA + secret + AgentCore worker
export AGENTCORE_A2A_ENDPOINT="https://..."   # from the `agentcore status` output
./infra/deploy_live.sh                # Cloud Run coordinator

export CURRENCY_COORDINATOR_ENDPOINT="https://currency-adk-coordinator-....run.app"
python3 -m evaluations.invoke_hosted "Convert 100 USD to EUR in verified mode."
```

The worker is not directly invocable with `agentcore invoke` in a useful way
any more: with `"protocol": "A2A"` it speaks JSON-RPC, and its authorizer
accepts only Google-issued tokens carrying the coordinator's email claim.
Drive the loop from the coordinator instead.

Select a non-default profile or region through the standard AWS environment
variables before configuring and deploying:

```bash
export AWS_PROFILE=my-profile
export AWS_REGION=us-east-1
./infra/configure_aws_target.sh
agentcore deploy -y
```

The generated target file contains the caller's AWS account ID and must remain
local. Run the configuration script again when switching profiles or regions.

## Worker configuration (AWS)

Runtime environment variables live in `agentcore/agentcore.json` under
`runtimes[0].envVars`:

- `CURRENCY_RATE_PROVIDER=frankfurter` — the worker's own rate source
- `BEDROCK_MODEL_ID` — override of the default inference profile
- `BEDROCK_MAX_TOKENS=1024` — explicit output cap for predictable Bedrock
  quota usage

The worker no longer carries `CURRENCY_A2A_ENDPOINT`: it is the remote agent,
not the caller.

Inbound authorization uses `authorizerType: "AWS_IAM"` — the default — so there
is no authorizer configuration block. The trust decision lives in IAM instead,
in two pieces created by `deploy_live.sh`:

- an OIDC identity provider for `https://accounts.google.com`, with the audience
  registered as a client ID;
- a role (`currencybench-coordinator` by default) whose trust policy allows
  `sts:AssumeRoleWithWebIdentity` only when **both** `accounts.google.com:aud`
  equals the audience and `accounts.google.com:sub` equals the coordinator
  service account's numeric `uniqueId`. The numeric subject is used rather than
  the email because it is immutable and never reused.

The role also carries an inline policy granting
`bedrock-agentcore:InvokeAgentRuntime` on the single deployed runtime ARN, not
`*`. That ARN is derived from `AGENTCORE_A2A_ENDPOINT`, which is why the script
attaches it on the second pass.

## Coordinator configuration (GCP)

Set on the Cloud Run service by `deploy_live.sh`:

- `CURRENCY_A2A_ENDPOINT` — the AgentCore runtime's A2A URL
- `CURRENCY_AWS_ROLE_ARN` — the role to assume; unset disables signing
- `CURRENCY_A2A_AUDIENCE` — audience requested from the metadata server
- `AWS_REGION` — SigV4 signing region, matching the worker's region
- `CURRENCY_REQUIRE_AWS_AGENTCORE=1` — fail closed for `a2a_only` and
  `verified` when the endpoint is missing
- `CURRENCY_RATE_PROVIDER=frankfurter`, `CURRENCY_RATE_TRANSPORT=mcp-stdio`
- `CURRENCY_TIMEOUT_SECONDS=60` (cross-cloud cold starts exceed the 10 s default)
- `GENAI_MODEL=gemini-2.5-flash`, `GOOGLE_API_KEY` from Secret Manager

A role ARN set without an audience or region raises rather than silently
sending unsigned requests.

## Historical baseline: AWS master → GCP worker

The results below were recorded before the 2026-07-30 reversal, when the
AgentCore runtime was the coordinator and the ADK agent the worker. They are
retained as the comparison target; no live GCP → AWS run has been recorded yet.

Observed deployment result on 2026-07-28:

- Stack `AgentCore-currencybench-default` deployed to us-east-1; runtime
  `currencybench_CurrencyCoordinator` active with CloudWatch logs at
  `/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT` and OTel traces.
- All three benchmark modes completed hosted. Verified mode (100 USD → EUR,
  CHF) returned both quotes with primary `mcp-stdio:frankfurter-live`
  (per-quote latency ~3.9 s including Cloud Run warm-up) and verifier
  `gcp-adk-a2a-worker` in agreement. a2a_only (500 EUR → USD) returned 1.1367
  from the Gemini agent, exactly matching mcp_only — both use Frankfurter
  upstream. `evaluations/invoke_hosted.py` (boto3 SigV4) round-tripped in
  ~12 s wall clock.
- Failure notes: Anthropic models on Bedrock require a one-time use-case
  form (`PutUseCaseForModelAccess`; `intendedUsers` is a numeric-code
  string) plus a marketplace agreement — switching to Nova Micro avoided
  both. The first A2A attempt failed with a protocol error because the ADK
  agent card advertises its bind address; see `coordinator/a2a_remote.py`.

Observed smoke test on 2026-07-29 after making Bedrock the explicit master:

- Deployed runtime:
  `currencybench_CurrencyCoordinator-vsXQSXExHv` in `us-east-1`.
- Natural-language `mcp_only`, `a2a_only`, and `verified` invocations all
  returned HTTP 200 through `InvokeAgentRuntime`.
- `mcp_only` returned the live `mcp-stdio:frankfurter-live` quote.
- `a2a_only` returned the live `gcp-adk-a2a-worker` quote.
- `verified` converted 100 USD to EUR and CHF. Both MCP and GCP ADK returned
  identical rates and amounts; deterministic comparison recorded
  `relative_difference: "0"` and `agreed: true` for both currencies, with no
  failures or warnings. Tool elapsed time was approximately 3.08 seconds.
- A first natural-language smoke request exposed Nova Micro asking to confirm
  an already-present target currency. The master prompt was corrected, a
  regression test was added, and the redeployed request called the benchmark
  tool without asking for confirmation.

Earlier baseline: the same benchmark ran hosted on Microsoft Foundry on
2026-07-27 (all three modes completed; verified mode agreed exactly with
relative_difference 0; mcp_only elapsed 0.71 s, verified 2.7 s).

## Resolved on the first live deployment (2026-07-30)

- **The A2A endpoint is the `/invocations` URL** that `agentcore status` prints,
  with the runtime ARN percent-encoded into the path:

  ```
  https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn%3Aaws%3A...%2F<id>/invocations
  ```

  The agent card is served at `<that URL>/.well-known/agent-card.json`. Dropping
  `/invocations` — the shape that looks more like a base URL — returns
  `UnknownOperationException`, as does using a bare runtime id instead of the
  encoded ARN.
- **SigV4 survives the AgentCore proxy.** A signed `GET` of the agent card
  returned 200, so the proxy does not rewrite the host or path in a way that
  invalidates the signature. This was the highest-risk assumption in the switch
  away from bearer tokens.
- **The percent-encoded ARN broke naive runtime-id parsing.** `deploy_live.sh`
  now URL-decodes the endpoint before extracting the id for the scoped invoke
  policy; splitting the raw string yielded the whole encoded ARN.
- **Google's `email` claim** is present, but only with `format=full`, which
  `coordinator/aws_identity.py` requests.
- **The worker's agent card advertises its container bind address**
  (`http://127.0.0.1:9000`), confirming the card-URL rewriting in
  `coordinator/a2a_remote.py` is required on this side too, not just for ADK.

## Script configuration

`deploy_live.sh` intentionally contains no project ID or key path. Set
`GCP_PROJECT`; optionally set `GCP_REGION`, `AWS_REGION`,
`CURRENCY_COORDINATOR_SERVICE`, `CURRENCY_COORDINATOR_SA_NAME`,
`CURRENCY_A2A_AUDIENCE`, `CURRENCY_AWS_ROLE_NAME`, `GEMINI_SECRET_NAME`, and
`GEMINI_KEY_FILE`. `GEMINI_KEY_FILE` is used only when creating a missing
Secret Manager secret.

It needs AWS credentials with IAM write access (`CreateRole`,
`CreateOpenIDConnectProvider`, `PutRolePolicy`) on the first run.

Before deployment, verify the latest official instructions:

- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-cli.html
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-inbound-auth.html
- https://github.com/aws/agentcore-cli
- https://strandsagents.com/docs/user-guide/quickstart/python/
- https://cloud.google.com/run/docs/securing/service-identity
