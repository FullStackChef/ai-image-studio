#!/usr/bin/env bash
# Build and push the slim ComfyUI worker image. Weights stay in S3.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

if ! command -v zip >/dev/null 2>&1; then
  echo "zip is required to pack the build context." >&2
  exit 1
fi

region=$(aws_region)
bucket=$(studio_bucket)
project=$(ssm_param /ai-image-studio/image-build-project)
workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

(
  cd "$ROOT"
  zip -qr "$workdir/source.zip" worker instance/download_models.py config/models.manifest.json \
    -x '*__pycache__*' '*.pyc'
)
aws s3 cp "$workdir/source.zip" "s3://${bucket}/builds/source.zip" --region "$region" --sse AES256
build_id=$(aws codebuild start-build --region "$region" --project-name "$project" --query build.id --output text)
echo "CodeBuild ${build_id}"

while true; do
  status=$(aws codebuild batch-get-builds --region "$region" --ids "$build_id" --query 'builds[0].buildStatus' --output text)
  echo "status=${status}"
  case "$status" in
    SUCCEEDED) exit 0 ;;
    FAILED|FAULT|STOPPED|TIMED_OUT)
      echo "Image build failed. Logs are in /ai-image-studio/image-build." >&2
      exit 1
      ;;
  esac
  sleep 20
done
