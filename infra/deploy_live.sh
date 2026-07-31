#!/bin/bash
# Deploy the full live cross-cloud loop, GCP master -> AWS worker:
#   1. Coordinator service account + Gemini key in GCP Secret Manager
#   2. AgentCore A2A worker -> Bedrock AgentCore Runtime (AWS)
#   3. adk_agent coordinator -> Cloud Run, pointed at the AgentCore endpoint
#
# The service account must exist before step 2 because AgentCore's CUSTOM_JWT
# authorizer pins that account's email as a token claim.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${GCP_PROJECT:?Set GCP_PROJECT to the target Google Cloud project ID}"
REGION="${GCP_REGION:-us-central1}"
SERVICE="${CURRENCY_COORDINATOR_SERVICE:-currency-adk-coordinator}"
GEMINI_SECRET="${GEMINI_SECRET_NAME:-gemini-api-key}"
GEMINI_KEY_FILE="${GEMINI_KEY_FILE:-}"
SA_NAME="${CURRENCY_COORDINATOR_SA_NAME:-currency-coordinator}"
SA_EMAIL="${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com"
# Must match allowedAudience in agentcore/agentcore.json.
AUDIENCE="${CURRENCY_A2A_AUDIENCE:-currencybench-agentcore-worker}"

echo "=== 1/3 Coordinator identity + Gemini key ==="
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project "$GCP_PROJECT" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --project "$GCP_PROJECT" \
    --display-name="Currency benchmark ADK coordinator"
fi
echo "Coordinator service account: $SA_EMAIL"

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

echo "=== 2/3 AgentCore A2A worker -> AWS ==="
cd "$REPO_ROOT"
# Pin the authorizer to this coordinator's identity. Audience alone only proves
# "some Google principal"; the email claim is what authorizes *this* caller.
python3 - "$SA_EMAIL" <<'EOF'
import json, sys
path = "agentcore/agentcore.json"
config = json.load(open(path))
authorizer = config["runtimes"][0]["authorizerConfiguration"]["customJwtAuthorizer"]
authorizer["customClaims"][0]["authorizingClaimMatchValue"]["claimMatchValue"][
    "matchValueString"
] = sys.argv[1]
json.dump(config, open(path, "w"), indent=2)
open(path, "a").write("\n")
print(f"Pinned authorizer email claim to {sys.argv[1]}")
EOF
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

echo "=== 3/3 ADK coordinator -> Cloud Run ==="
./infra/sync_adk.sh
gcloud run deploy "$SERVICE" \
  --project "$GCP_PROJECT" --region "$REGION" \
  --source "$REPO_ROOT/adk_agent" \
  --service-account "$SA_EMAIL" \
  --allow-unauthenticated \
  --min-instances=0 --max-instances=2 --memory=1Gi --cpu=1 \
  --set-secrets "GOOGLE_API_KEY=${GEMINI_SECRET}:latest" \
  --set-env-vars "^@^GENAI_MODEL=gemini-2.5-flash@GOOGLE_GENAI_USE_VERTEXAI=false@CURRENCY_A2A_ENDPOINT=${AGENTCORE_A2A_ENDPOINT}@CURRENCY_A2A_AUDIENCE=${AUDIENCE}@CURRENCY_REQUIRE_AWS_AGENTCORE=1@CURRENCY_RATE_PROVIDER=frankfurter@CURRENCY_RATE_TRANSPORT=mcp-stdio@CURRENCY_TIMEOUT_SECONDS=60"
COORDINATOR_URL="$(gcloud run services describe "$SERVICE" --project "$GCP_PROJECT" --region "$REGION" --format='value(status.url)')"
echo "Cloud Run coordinator: $COORDINATOR_URL"
curl -sf "$COORDINATOR_URL/health" && echo " <- health OK"

echo
echo "=== Done. Smoke-test the loop: ==="
echo "  export CURRENCY_COORDINATOR_ENDPOINT=\"$COORDINATOR_URL\""
echo "  python3 -m evaluations.invoke_hosted \"Convert 100 USD to EUR in verified mode.\""
