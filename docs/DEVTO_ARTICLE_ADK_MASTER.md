---
title: "Google ADK as the Master Agent, Calling Amazon Bedrock over A2A"
published: false
series: A2A
tags: aiagent, googleadk, a2aprotocol, aws
cover_image: devto-cover-adk-master.png
---

This is the third run of the same cross-cloud currency benchmark, and the first
with the arrow pointing the other way. A **Google ADK master** on Cloud Run
(Gemini 2.5 Flash, `us-central1`) owns the benchmark policy. It calls an
**Amazon Bedrock AgentCore worker** (Strands Agents on Nova Micro, `us-east-1`)
over **A2A v1.0**, and cross-checks the answer against an MCP exchange-rate
tool.

The previous run had Bedrock as the master and ADK as the worker. Reversing it
was supposed to be a deployment change — the domain core is
framework-independent, so the comparison logic never moved. What actually
happened is that reversal exposed six interoperability defects, **five of which
no local test could have caught**. That is the interesting part, so this article
leads with the failures.

#### What is this project trying to do?

Most A2A demos stop at "the HTTP 200 came back." That is a smoke test, not an
interoperability benchmark.

The benchmark runs three modes so the cost of remote verification is separable
from the cost of the work:

| Mode | What runs | Purpose |
|---|---|---|
| `mcp_only` | ADK master calls the rate tool over MCP stdio | Baseline |
| `a2a_only` | ADK master delegates to the AgentCore worker | Remote-agent cost |
| `verified` | Both, concurrently, then compared deterministically | Accuracy/overhead |

Comparison is arithmetic, not a model judgement. Amounts and rates are
`Decimal`. A model is never asked whether two numbers agree.

---

## Six things that broke

### 1. The Strands A2A extra speaks the wrong protocol version

This one was caught before deploying, and it dictated the whole worker design.

`strands-agents[a2a]` pins `a2a-sdk<0.4` — the A2A **v0.3** wire methods
(`message/send`). `google-adk[a2a]==2.5.0` uses `a2a-sdk` **1.x** — v1.0
(`SendMessage`). A v1.0 client cannot call a v0.3 server, and the agent card
carries no version negotiation.

This is the same split that forced the A2UI extension out of the ADK agent in
the first run of this project. It reappeared from the opposite side.

The fix: use `strands-agents` for the agent loop only, and build the A2A v1.0
server surface from `a2a-sdk` directly.

```python
# app/CurrencyWorker/main.py -- NOT strands.multiagent.a2a.A2AServer
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes

app = Starlette(routes=[
    *create_agent_card_routes(AGENT_CARD),
    *create_jsonrpc_routes(_request_handler, rpc_url="/"),
])
```

A test asserts the lockfile keeps `a2a-sdk` on 1.x, so a dependency bump cannot
quietly reintroduce the mismatch.

### 2. `google-adk[a2a]` does not install its own server dependency

The Cloud Run container built fine, passed every local test, and then died on
startup:

```
ModuleNotFoundError: No module named 'sse_starlette'
  File ".../google/adk/a2a/_compat.py", line 769, in attach_a2a_routes_to_app
```

ADK's `to_a2a()` imports `a2a.server.routes`, which needs `sse_starlette`, which
arrives only with the `a2a-sdk[http-server]` extra. Neither `google-adk[a2a]`
nor bare `a2a-sdk` pulls it in.

Cost: one failed rollout. Both halves now declare `a2a-sdk[http-server]`.

### 3. The IAM trust policy could never have matched

The coordinator holds no AWS keys. It federates: Cloud Run's metadata server
mints a Google OIDC token, STS `AssumeRoleWithWebIdentity` exchanges it for
temporary credentials, and requests are SigV4-signed.

The trust policy looked obviously correct and was silently impossible:

```json
"Condition": { "StringEquals": {
  "accounts.google.com:aud": "currencybench-agentcore-worker",
  "accounts.google.com:sub": "1019138736740282766.."
}}
```

AWS does not map those condition keys to the claims their names suggest:

| Condition key | Actual Google claim |
|---|---|
| `accounts.google.com:oaud` | the token's `aud` |
| `accounts.google.com:aud` | the token's **`azp`** (numeric client id) |
| `accounts.google.com:sub` | the token's `sub` |

The audience *string* was being compared against a *number*. Pin the audience
with `oaud`, not `aud`.

### 4. Creating an OIDC provider for Google breaks Google federation

The natural next step — register `accounts.google.com` as an IAM OIDC identity
provider — is wrong. AWS federates with Google natively. Creating an explicit
provider switched STS to validating against that provider's thumbprint, and
every exchange failed:

```xml
<Code>InvalidIdentityToken</Code>
<Message>The web identity token provided could not be validated.</Message>
```

Deleting the provider fixed it. The principal is the bare domain:

```json
"Principal": { "Federated": "accounts.google.com" }
```

Worth noting the failure mode: an invalid *token* and a rejected *trust policy*
are different errors (`InvalidIdentityToken` vs `AccessDenied`), and that
distinction is the fastest way to tell these two bugs apart.

### 5. AgentCore strips the A2A version header, and the SDK defaults to v0.3

With federation working and SigV4 signing accepted, the worker rejected its own
correctly-versioned client:

```
A2A version '0.3' is not supported by this handler. Expected version '1.0'.
```

The client was `a2a-sdk` 1.1.2 and does send the right header
(`client_factory.py` sets `A2A-Version: 1.0`). The worker was v1.0. The card
advertised `"protocolVersion": "1.0"`.

The header never arrived. AgentCore's A2A runtime forwards only allow-listed
request headers — and the server-side validator treats a **missing** header as
v0.3:

```python
# a2a/utils/version_validator.py
if not actual_version:
    return constants.PROTOCOL_VERSION_0_3
```

A dropped header and an old client are indistinguishable to the server. The fix
is one line of runtime config:

```json
"requestHeaderAllowlist": ["A2A-Version"]
```

This is the version-skew theme of the whole project arriving a third way:
first through a transitive pin, then through a framework extra, now through a
proxy.

### 6. Agent cards advertise bind addresses, in both directions

The AgentCore worker's card advertises `http://127.0.0.1:9000` — its container's
own bind address. ADK's `to_a2a()` does the same with `http://127.0.0.1:8080`.
`a2a-sdk` clients route by card URL, so cross-cloud calls fail unless the client
rewrites the interfaces to the endpoint it actually dialled.

Documented in the first run for ADK; it is not an ADK quirk.

### Bonus: the A2A endpoint is not the URL that looks like a base URL

`agentcore status` prints a URL with the runtime ARN percent-encoded into the
path and an `/invocations` suffix. That full URL, suffix included, is the A2A
base:

```
https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn%3Aaws%3A...%2F<id>/invocations
                                                                       └── card at <base>/.well-known/agent-card.json
```

Dropping `/invocations` — the tidier-looking base — returns
`UnknownOperationException`. So does using a bare runtime id instead of the
encoded ARN. The percent-encoding also breaks naive `${VAR##*/runtimes/}` shell
parsing when deriving the runtime id for a scoped IAM policy.

---

## What it measures

Five warm invocations per mode, driven through the deployed Cloud Run
coordinator, `us-central1` → `us-east-1`, on 2026-07-31:

| Mode | Median | Min | Max |
|---|---|---|---|
| `mcp_only` | **2.96 s** | 2.37 s | 3.84 s |
| `a2a_only` | **18.92 s** | 16.30 s | 19.68 s |
| `verified` | **16.19 s** | 15.64 s | 22.86 s |

Two things stand out.

**The remote-agent hop costs about 6× the tool baseline.** That is not network
latency — `us-central1` to `us-east-1` is tens of milliseconds. It is a second
model turn: the worker is an agent, so delegation means Nova Micro plans a tool
call, calls it, and writes a structured reply, on top of Gemini doing the same.
A2A delegation buys independence, and independence costs an inference.

**`verified` is not slower than `a2a_only`.** Verified mode does the MCP call
*and* the A2A call and compares them, yet its median is lower. The coordinator
issues both concurrently:

```python
mcp_task = self._call("mcp", self._mcp, request, failures)
a2a_task = self._call("a2a", self._a2a, request, failures)
mcp_quotes, a2a_quotes = await asyncio.gather(mcp_task, a2a_task)
```

So verified ≈ max(mcp, a2a), not the sum. Independent verification is close to
free once you are already paying for the remote agent. The two medians differ by
less than the run-to-run spread, so read them as equal, not as verified being
genuinely faster.

Correctness, verified mode, 100 USD → EUR and CHF:

```
EUR  rate 0.87138  ->  87.138    CHF  rate 0.81248  ->  81.248
primary:  mcp-stdio:frankfurter-live
verifier: aws-agentcore-a2a-worker
```

Both targets agreed. Both also carried `rate is stale; check the observation
timestamp` — the MCP side returned Frankfurter's daily reference rate stamped
`2026-07-30T00:00:00Z` against a run at `04:09Z` on the 31st, over the 24 h
threshold. The staleness rule firing on a real published rate, rather than a
fixture, is the more useful signal here.

---

## What this run does not establish

- **The IAM policy is not yet least-privilege.** Scoping
  `bedrock-agentcore:InvokeAgentRuntime` to `runtime/<id>` and `runtime/<id>/*`
  was denied with 403; only `Resource: "*"` worked. AgentCore authorises against
  some ARN shape I have not identified, and data-plane events are not in
  CloudTrail by default. The measurements above were taken with the wide policy.
- **n is 5, one region pair, one day, one model pair.** No p95, no cost
  accounting, no token counts, no cold/warm distribution. The 38-case × 3-mode
  matrix has not been run in this direction.
- **Nothing here is a claim about which cloud is faster.** Gemini 2.5 Flash and
  Nova Micro are different models at different price points; the 6× is the cost
  of *agent delegation*, not of AWS.

## The part worth keeping

The domain core did not change. `CurrencyCoordinator` takes two duck-typed
adapters, so which cloud hosts the master is a deployment decision. That held
up: the comparison logic, the staleness rule, the failure taxonomy, and the
evaluation harness all survived the reversal untouched.

Everything that broke was at the seams — protocol versions, dependency extras,
identity federation, header forwarding, URL shapes. Five of the six defects were
invisible until something real was deployed. If you take one thing from this:
**an interoperability test that never leaves your laptop is testing your
mocks.**
