#!/usr/bin/env bash
# Create the private bucket, budget alarm, and least-privilege IAM user.
# Does not create RunPod resources and does not print secrets.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

cd "$ROOT/infra"
export TF_IN_AUTOMATION=1
build_tf_args
terraform init -input=false
terraform apply "${TF_ARGS[@]}"
terraform output
