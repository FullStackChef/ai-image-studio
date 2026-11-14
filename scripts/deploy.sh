#!/usr/bin/env bash
# Create or update the isolated stack. Does not print HF_TOKEN.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

cd "$ROOT/infra"
export TF_IN_AUTOMATION=1
build_tf_args
terraform init -input=false
terraform apply "${TF_ARGS[@]}"

if [[ -n "${HF_TOKEN:-}" ]]; then
  region=$(terraform output -raw region)
  token_file=$(mktemp)
  chmod 600 "$token_file"
  TOKEN_FILE="$token_file" python3 - <<'PY'
import json
import os

with open(os.environ["TOKEN_FILE"], "w", encoding="utf-8") as handle:
    json.dump(
        {
            "Name": "/ai-image-studio/hf-token",
            "Type": "SecureString",
            "Value": os.environ["HF_TOKEN"],
            "Overwrite": True,
        },
        handle,
    )
PY
  aws ssm put-parameter --region "$region" --cli-input-json "file://${token_file}" >/dev/null
  rm -f "$token_file"
  echo "Stored the Hugging Face token in SSM Parameter Store."
fi

terraform output
