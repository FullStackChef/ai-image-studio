#!/usr/bin/env bash
# Delete the studio VPC, instance, model volume, and private bucket.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

if [[ "${CONFIRM_DESTROY:-}" != "yes" ]]; then
  echo "This deletes the GPU instance, the model volume, and the private bucket, including generated images." >&2
  echo "Copy anything you want to keep out of the bucket first, then re-run with CONFIRM_DESTROY=yes." >&2
  exit 1
fi

cd "$ROOT/infra"
export TF_IN_AUTOMATION=1
build_tf_args
terraform init -input=false
terraform destroy "${TF_ARGS[@]}"
