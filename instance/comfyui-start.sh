#!/bin/bash
set -euo pipefail
set -a
# shellcheck disable=SC1091
source /etc/ai-image-studio.env
set +a
cd /data/ComfyUI
# COMFY_ARGS is an optional owner-controlled string from the env file.
# shellcheck disable=SC2086
exec ./venv/bin/python main.py \
  --listen 127.0.0.1 \
  --port "${COMFY_PORT:-8188}" \
  --output-directory /data/ComfyUI/output \
  --input-directory /data/ComfyUI/input \
  --feature-flag local_web_sso=false \
  ${COMFY_ARGS:-}
