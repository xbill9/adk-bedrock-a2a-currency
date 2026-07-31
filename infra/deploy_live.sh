#!/bin/bash
# Deploy the full live cross-cloud loop:
#   1. Gemini key -> GCP Secret Manager
#   2. adk_agent (A2A v1.0 + colocated MCP rate server) -> Cloud Run
#   3. AgentCore-hosted coordinator redeploy pointed at the Cloud Run endpoint
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${GCP_PROJECT:?Set GCP_PROJECT to the target Google Cloud project ID}"
REGION="${GCP_REGION:-us-central1}"
SERVICE="${CURRENCY_A2A_SERVICE:-currency-adk-a2a}"
GEMINI_SECRET="${GEMINI_SECRET_NAME:-gemini-api-key}"
GEMINI_KEY_FILE="${GEMINI_KEY_FILE:-}"

echo "=== 1/3 Gemini key -> Secret Manager ==="
if ! gcloud secrets describe "$GEMINI_SECRET" --project "$GCP_PROJECT" >/dev/null 2>&1; then
  if [[ -z "$GEMINI_KEY_FILE" ]]; then
    echo "GEMINI_SECRET_NAME does not exist; set GEMINI_KEY_FILE to create it." >&2
    exit 2
  fi
  gcloud secrets create "$GEMINI_SECRET" --project "$GCP_PROJECT" \
    --replication-policy=automatic --data-file="$GEMINI_KEY_FILE"
fi
COMPUTE_SA="$(gcloud projects describe "$GCP_PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
gcloud secrets add-iam-policy-binding "$GEMINI_SECRET" --project "$GCP_PROJECT" \
  --member="serviceAccount:${COMPUTE_SA}" --role=roles/secretmanager.secretAccessor >/dev/null

echo "=== 2/3 ADK agent -> Cloud Run ==="
gcloud run deploy "$SERVICE" \
  --project "$GCP_PROJECT" --region "$REGION" \
  --source "$REPO_ROOT/adk_agent" \
  --allow-unauthenticated \
  --min-instances=0 --max-instances=2 --memory=1Gi --cpu=1 \
  --set-secrets "GOOGLE_API_KEY=${GEMINI_SECRET}:latest" \
  --set-env-vars "MCP_SERVER_URL=http://127.0.0.1:8081/mcp,GENAI_MODEL=gemini-2.5-flash,GOOGLE_GENAI_USE_VERTEXAI=false"
A2A_URL="$(gcloud run services describe "$SERVICE" --project "$GCP_PROJECT" --region "$REGION" --format='value(status.url)')"
echo "Cloud Run A2A endpoint: $A2A_URL"
curl -sf "$A2A_URL/health" && echo " <- health OK"

echo "=== 3/3 AgentCore coordinator redeploy ==="
cd "$REPO_ROOT"
# Runtime env vars live in agentcore/agentcore.json (runtimes[0].envVars).
# Rewrite CURRENCY_A2A_ENDPOINT there in case the Cloud Run URL changed.
python3 - "$A2A_URL" <<'EOF'
import json, sys
path = "agentcore/agentcore.json"
config = json.load(open(path))
env = config["runtimes"][0].setdefault("envVars", [])
for var in env:
    if var["name"] == "CURRENCY_A2A_ENDPOINT":
        var["value"] = sys.argv[1]
        break
else:
    env.append({"name": "CURRENCY_A2A_ENDPOINT", "value": sys.argv[1]})
json.dump(config, open(path, "w"), indent=2)
EOF
./infra/sync_app.sh
agentcore deploy -y
agentcore status
agentcore invoke "Convert 100 USD to EUR in verified mode."

echo "=== Done. Both halves deployed. ==="
