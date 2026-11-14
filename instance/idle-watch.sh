#!/bin/bash
# Stop this instance after the configured idle window. Activity is an open
# ComfyUI connection, a non-empty queue, or GPU utilization at 15% or more.
set -euo pipefail
set -a
# shellcheck disable=SC1091
source /etc/ai-image-studio.env
set +a

STATE_DIR=/var/lib/ai-image-studio
HEARTBEAT="${STATE_DIR}/last_activity"
TICKS="${STATE_DIR}/idle_ticks"
LOG=/var/log/ai-image-studio-idle.log
mkdir -p "$STATE_DIR"

if [[ ! -f "${STATE_DIR}/setup-complete" ]]; then
  echo "$(date -Is) setup incomplete; not stopping" >> "$LOG"
  exit 0
fi

minutes="${IDLE_MINUTES}"
ssm_minutes=$(aws ssm get-parameter \
  --region "$AWS_REGION" \
  --name /ai-image-studio/idle-minutes \
  --query Parameter.Value \
  --output text 2>/dev/null || true)
if [[ "$ssm_minutes" =~ ^[0-9]+$ ]]; then
  minutes=$ssm_minutes
fi

sync_outputs() {
  if [[ -d /data/ComfyUI/output ]]; then
    aws s3 sync /data/ComfyUI/output "s3://${BUCKET}/outputs/comfy/" \
      --exclude "*" --include "*.png" --include "*.jpg" --include "*.jpeg" --include "*.webp" \
      --only-show-errors || true
  fi
}

active=0
if command -v ss >/dev/null 2>&1; then
  if ss -H -tn state established 'sport = :8188' | grep -q .; then
    active=1
  fi
fi

queue=$(curl -fsS --max-time 5 http://127.0.0.1:8188/queue || true)
if [[ -n "$queue" ]]; then
  if python3 -c 'import json,sys; data=json.loads(sys.argv[1]); raise SystemExit(0 if data.get("queue_running") or data.get("queue_pending") else 1)' "$queue"; then
    active=1
  fi
fi

util=0
if command -v nvidia-smi >/dev/null 2>&1; then
  util=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -n 1 | tr -d '[:space:]' || true)
fi
if [[ "$util" =~ ^[0-9]+$ ]] && (( util >= 15 )); then
  active=1
fi

now=$(date +%s)
if [[ ! -f "$HEARTBEAT" || "$active" -eq 1 ]]; then
  echo "$now" > "$HEARTBEAT"
fi

ticks=0
if [[ -f "$TICKS" ]]; then
  ticks=$(cat "$TICKS")
fi
if [[ ! "$ticks" =~ ^[0-9]+$ ]]; then
  ticks=0
fi
ticks=$((ticks + 1))
echo "$ticks" > "$TICKS"
if (( ticks % 10 == 0 )); then
  sync_outputs
fi

if [[ "$active" -eq 1 ]]; then
  exit 0
fi

last=$(cat "$HEARTBEAT")
idle_for=$((now - last))
limit=$((minutes * 60))
if (( idle_for >= limit )); then
  echo "$(date -Is) idle for ${idle_for}s (limit ${limit}s); stopping" >> "$LOG"
  sync_outputs
  exec /usr/local/sbin/ai-image-studio-stop
fi
