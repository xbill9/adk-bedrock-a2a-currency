#!/usr/bin/env bash
set -euo pipefail

AWS_TARGET_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
if [[ -z "$AWS_TARGET_REGION" ]]; then
  AWS_TARGET_REGION="$(aws configure get region)"
fi
if [[ -z "$AWS_TARGET_REGION" ]]; then
  echo "Set AWS_REGION or configure a default region for the active AWS profile." >&2
  exit 1
fi

AWS_TARGET_ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
if [[ ! "$AWS_TARGET_ACCOUNT" =~ ^[0-9]{12}$ ]]; then
  echo "Could not resolve a valid AWS account from the active AWS credentials." >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
TARGET_FILE="$REPO_ROOT/agentcore/aws-targets.json"

python3 - "$TARGET_FILE" "$AWS_TARGET_ACCOUNT" "$AWS_TARGET_REGION" <<'PY'
import json
import sys
from pathlib import Path

target_file = Path(sys.argv[1])
target = [
    {
        "name": "default",
        "account": sys.argv[2],
        "region": sys.argv[3],
    }
]
target_file.write_text(json.dumps(target, indent=2) + "\n", encoding="utf-8")
PY

echo "Configured AgentCore target 'default' for account $AWS_TARGET_ACCOUNT in $AWS_TARGET_REGION."
