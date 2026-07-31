# AgentCore deployment

Deploy the coordinator to Amazon Bedrock AgentCore Runtime with the AgentCore
CLI (`npm install -g @aws/agentcore`, Node 20+, CDK-based). The hosted
coordinator uses the AgentCore execution role and the AWS credential chain;
do not commit API keys.

Provision only the MVP resources:

- AgentCore Runtime hosting the Strands coordinator (`main.py`);
- Bedrock model access for the chosen Claude inference profile;
- the CLI-created IAM execution role and CloudWatch log group;
- CDK bootstrap assets (S3 staging for CodeZip builds).

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
--memory none` and relocated into the repo root:

- `agentcore/agentcore.json` — project config, including the runtime `envVars`
  for the live cross-cloud loop (committed);
- `agentcore/aws-targets.json` — account-specific deployment target generated
  locally by `infra/configure_aws_target.sh` and excluded from Git;
- `agentcore/.env.local`, `agentcore/.cli/` — local state (not committed);
- `app/CurrencyCoordinator/` — the deployable app: `main.py`
  (`BedrockAgentCoreApp` entrypoint), `model/load.py`, its own
  `pyproject.toml`/`uv.lock`, plus copies of `coordinator/` and `mcp_server/`
  synced by `infra/sync_app.sh` (the copies are build artifacts; edit the
  repo-root packages).

## Deploy and smoke-test

```bash
./infra/configure_aws_target.sh       # uses the active AWS profile/account
./infra/sync_app.sh                   # refresh the app's package copies
agentcore deploy -y                   # CDK deploy; prints the runtime ARN
agentcore status
agentcore invoke "Convert 100 USD to EUR in verified mode."
```

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

Runtime environment variables live in `agentcore/agentcore.json` under
`runtimes[0].envVars`:

- `CURRENCY_A2A_ENDPOINT` — Cloud Run URL of the ADK agent
- `CURRENCY_REQUIRE_GCP_ADK=1` — fail closed for `a2a_only` and `verified`
  when the Cloud Run endpoint is missing
- `CURRENCY_RATE_PROVIDER=frankfurter`
- `CURRENCY_RATE_TRANSPORT=mcp-stdio`
- `CURRENCY_TIMEOUT_SECONDS=60` (Cloud Run cold starts exceed the 10 s default)
- `BEDROCK_MODEL_ID` — override of the default inference profile
- `BEDROCK_MAX_TOKENS=1024` — explicit output cap for predictable Bedrock
  quota usage

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

Historical baseline: the same benchmark ran hosted on Microsoft Foundry on
2026-07-27 (all three modes completed; verified mode agreed exactly with
relative_difference 0; mcp_only elapsed 0.71 s, verified 2.7 s). That result
is retained as the comparison target for the first AgentCore run; no AWS
end-to-end result has been recorded yet.

`deploy_live.sh` intentionally contains no project ID or key path. Set
`GCP_PROJECT`; optionally set `GCP_REGION`, `CURRENCY_A2A_SERVICE`,
`GEMINI_SECRET_NAME`, and `GEMINI_KEY_FILE`. `GEMINI_KEY_FILE` is used only
when creating a missing Secret Manager secret.

Before deployment, verify the latest official instructions:

- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-cli.html
- https://github.com/aws/agentcore-cli
- https://strandsagents.com/docs/user-guide/quickstart/python/
