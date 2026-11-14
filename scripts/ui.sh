#!/usr/bin/env bash
# Forward ComfyUI to http://127.0.0.1:8188 and hold the tunnel open.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"
id=$(studio_instance_id)
region=$(aws_region)
echo "Open http://127.0.0.1:8188 while this command is running. Ctrl-C closes the tunnel."
exec aws ssm start-session \
  --region "$region" \
  --target "$id" \
  --document-name AWS-StartPortForwardingSession \
  --parameters portNumber=8188,localPortNumber=8188
