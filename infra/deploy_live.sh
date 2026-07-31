#!/bin/bash
# Deploy the full live cross-cloud loop, GCP master -> AWS worker:
#   1. Coordinator service account + Gemini key in GCP Secret Manager
#   2. AWS IAM: Google OIDC provider + role the coordinator may assume
#   3. AgentCore A2A worker -> Bedrock AgentCore Runtime (AWS)
#   4. adk_agent coordinator -> Cloud Run, pointed at the AgentCore endpoint
#
# The service account must exist before step 2 because the role's trust policy
# pins that account's numeric subject ID.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${GCP_PROJECT:?Set GCP_PROJECT to the target Google Cloud project ID}"
REGION="${GCP_REGION:-us-central1}"
SERVICE="${CURRENCY_COORDINATOR_SERVICE:-currency-adk-coordinator}"
GEMINI_SECRET="${GEMINI_SECRET_NAME:-gemini-api-key}"
GEMINI_KEY_FILE="${GEMINI_KEY_FILE:-}"
SA_NAME="${CURRENCY_COORDINATOR_SA_NAME:-currency-coordinator}"
SA_EMAIL="${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com"
AUDIENCE="${CURRENCY_A2A_AUDIENCE:-currencybench-agentcore-worker}"
AWS_ROLE_NAME="${CURRENCY_AWS_ROLE_NAME:-currencybench-coordinator}"
AWS_REGION="${AWS_REGION:-us-east-1}"

echo "=== 1/4 Coordinator identity + Gemini key ==="
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project "$GCP_PROJECT" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --project "$GCP_PROJECT" \
    --display-name="Currency benchmark ADK coordinator"
fi
# The numeric unique ID, not the email: it is immutable and never reused, so a
# deleted-and-recreated account cannot inherit this role.
SA_SUBJECT="$(gcloud iam service-accounts describe "$SA_EMAIL" \
  --project "$GCP_PROJECT" --format='value(uniqueId)')"
echo "Coordinator service account: $SA_EMAIL (sub=$SA_SUBJECT)"

if ! gcloud secrets describe "$GEMINI_SECRET" --project "$GCP_PROJECT" >/dev/null 2>&1; then
  if [[ -z "$GEMINI_KEY_FILE" ]]; then
    echo "GEMINI_SECRET_NAME does not exist; set GEMINI_KEY_FILE to create it." >&2
    exit 2
  fi
  gcloud secrets create "$GEMINI_SECRET" --project "$GCP_PROJECT" \
    --replication-policy=automatic --data-file="$GEMINI_KEY_FILE"
fi
gcloud secrets add-iam-policy-binding "$GEMINI_SECRET" --project "$GCP_PROJECT" \
  --member="serviceAccount:${SA_EMAIL}" --role=roles/secretmanager.secretAccessor >/dev/null

echo "=== 2/4 AWS IAM: assumable role for the Google identity ==="
AWS_ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

# No IAM OIDC identity provider is created: AWS federates with
# accounts.google.com natively, so the principal is the bare domain.
#
# The condition keys do NOT map to the claims their names suggest, which is the
# single easiest thing to get wrong here:
#
#   accounts.google.com:oaud  ->  the token's `aud`  (our audience string)
#   accounts.google.com:aud   ->  the token's `azp`  (the SA's numeric client id)
#   accounts.google.com:sub   ->  the token's `sub`  (the SA's numeric unique id)
#
# Putting the audience string in `:aud` is checked against `azp`, a numeric
# value, so it can never match and STS returns "Incorrect token audience".
# Pinning oaud + sub gives the full property we want: the right audience AND
# the exact caller identity.
TRUST_POLICY="$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Federated": "accounts.google.com" },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "accounts.google.com:oaud": "${AUDIENCE}",
          "accounts.google.com:sub": "${SA_SUBJECT}"
        }
      }
    }
  ]
}
JSON
)"

if aws iam get-role --role-name "$AWS_ROLE_NAME" >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name "$AWS_ROLE_NAME" \
    --policy-document "$TRUST_POLICY"
  echo "Updated trust policy on role $AWS_ROLE_NAME"
else
  aws iam create-role --role-name "$AWS_ROLE_NAME" \
    --description "Assumed by the GCP currency benchmark coordinator" \
    --assume-role-policy-document "$TRUST_POLICY" >/dev/null
  echo "Created role $AWS_ROLE_NAME"
fi
AWS_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT}:role/${AWS_ROLE_NAME}"

echo "=== 3/4 AgentCore A2A worker -> AWS ==="
cd "$REPO_ROOT"
./infra/sync_app.sh
agentcore deploy -y
agentcore status

# The A2A endpoint is whatever AgentCore fronts the runtime with. The CLI output
# format is not stable enough to parse blindly, so require it explicitly.
if [[ -z "${AGENTCORE_A2A_ENDPOINT:-}" ]]; then
  cat >&2 <<'MSG'

Set AGENTCORE_A2A_ENDPOINT to the worker's A2A URL from the `agentcore status`
output above, then re-run this script to deploy the coordinator. Example:

  export AGENTCORE_A2A_ENDPOINT="https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/<runtime-id>"
MSG
  exit 2
fi
echo "AgentCore A2A endpoint: $AGENTCORE_A2A_ENDPOINT"

# Scope invocation to the deployed runtime. `agentcore status` reports the URL
# with a percent-encoded ARN in the path (.../runtimes/arn%3A...%2F<id>/invocations),
# so decode before extracting rather than splitting on the raw string.
RUNTIME_ID="$(python3 - "$AGENTCORE_A2A_ENDPOINT" <<'PY'
import sys, urllib.parse
url = urllib.parse.unquote(sys.argv[1]).split("?")[0]
tail = url.split("/runtimes/", 1)[-1]
tail = tail.split("/invocations")[0]
# Handles both a bare runtime id and a full runtime ARN in the path.
print(tail.rsplit("runtime/", 1)[-1].split("/")[0])
PY
)"
if [[ -z "$RUNTIME_ID" ]]; then
  echo "Could not derive a runtime id from AGENTCORE_A2A_ENDPOINT" >&2
  exit 2
fi
aws iam put-role-policy --role-name "$AWS_ROLE_NAME" \
  --policy-name invoke-currency-worker \
  --policy-document "$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "bedrock-agentcore:InvokeAgentRuntime",
      "Resource": [
        "arn:aws:bedrock-agentcore:${AWS_REGION}:${AWS_ACCOUNT}:runtime/${RUNTIME_ID}",
        "arn:aws:bedrock-agentcore:${AWS_REGION}:${AWS_ACCOUNT}:runtime/${RUNTIME_ID}/*"
      ]
    }
  ]
}
JSON
)"
echo "Scoped invoke policy to runtime $RUNTIME_ID"

echo "=== 4/4 ADK coordinator -> Cloud Run ==="
./infra/sync_adk.sh
gcloud run deploy "$SERVICE" \
  --project "$GCP_PROJECT" --region "$REGION" \
  --source "$REPO_ROOT/adk_agent" \
  --service-account "$SA_EMAIL" \
  --allow-unauthenticated \
  --min-instances=0 --max-instances=2 --memory=1Gi --cpu=1 \
  --set-secrets "GOOGLE_API_KEY=${GEMINI_SECRET}:latest" \
  --set-env-vars "^@^GENAI_MODEL=gemini-2.5-flash@GOOGLE_GENAI_USE_VERTEXAI=false@CURRENCY_A2A_ENDPOINT=${AGENTCORE_A2A_ENDPOINT}@CURRENCY_A2A_AUDIENCE=${AUDIENCE}@CURRENCY_AWS_ROLE_ARN=${AWS_ROLE_ARN}@AWS_REGION=${AWS_REGION}@CURRENCY_REQUIRE_AWS_AGENTCORE=1@CURRENCY_RATE_PROVIDER=frankfurter@CURRENCY_RATE_TRANSPORT=mcp-stdio@CURRENCY_TIMEOUT_SECONDS=60"
COORDINATOR_URL="$(gcloud run services describe "$SERVICE" --project "$GCP_PROJECT" --region "$REGION" --format='value(status.url)')"
echo "Cloud Run coordinator: $COORDINATOR_URL"
curl -sf "$COORDINATOR_URL/health" && echo " <- health OK"

echo
echo "=== Done. Smoke-test the loop: ==="
echo "  export CURRENCY_COORDINATOR_ENDPOINT=\"$COORDINATOR_URL\""
echo "  python3 -m evaluations.invoke_hosted \"Convert 100 USD to EUR in verified mode.\""
