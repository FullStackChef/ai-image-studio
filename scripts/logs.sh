#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"
if [[ -z "${S3_BUCKET:-}" ]]; then
  echo "Set S3_BUCKET to the terraform output bucket_name." >&2
  exit 1
fi
aws s3 ls "s3://${S3_BUCKET}/outputs/" --region "$(aws_region)"
