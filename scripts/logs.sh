#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"
region=$(aws_region)
bucket=$(studio_bucket)
aws s3 ls "s3://${bucket}/logs/" --region "$region"
