#!/usr/bin/env bash
# Build the slim RunPod worker image locally. Does not push and does not call RunPod or AWS.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
IMAGE="${IMAGE:-ai-image-studio-worker}"
if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required to build the worker image." >&2
  exit 1
fi
docker build -f "$ROOT/worker/Dockerfile" -t "$IMAGE" "$ROOT"
echo "Built ${IMAGE}. Push it to the registry attached to your RunPod endpoint, then set the endpoint image to that tag."
