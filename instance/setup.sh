#!/bin/bash
# Idempotent ComfyUI install. Re-run with --no-stop after the instance is up
# to fetch weights that were added to the manifest later.
set -euo pipefail
exec > >(tee -a /var/log/ai-image-studio-setup.log) 2>&1

NO_STOP=0
for arg in "$@"; do
  if [[ "$arg" == "--no-stop" ]]; then
    NO_STOP=1
  else
    echo "unknown argument: $arg" >&2
    exit 1
  fi
done

set -a
# shellcheck disable=SC1091
source /etc/ai-image-studio.env
set +a

on_fail() {
  status=$?
  echo "setup failed with status ${status}" >&2
  if [[ -x /usr/local/sbin/ai-image-studio-stop ]]; then
    /usr/local/sbin/ai-image-studio-stop || true
  fi
  exit "$status"
}
trap on_fail ERR

echo "setup start $(date -Is)"

if ! id ubuntu >/dev/null 2>&1; then
  echo "the DLAMI should provide an ubuntu user" >&2
  exit 1
fi
if ! command -v nvidia-smi >/dev/null 2>&1 || ! nvidia-smi; then
  echo "nvidia-smi failed. The AMI must be the Deep Learning Base GPU AMI." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git python3 python3-venv python3-pip python3-dev build-essential wget ca-certificates

find_data_device() {
  local want="${DATA_VOLUME_ID//-/}"
  local byid="/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${want}"
  local name serial compact
  if [[ -e "$byid" ]]; then
    readlink -f "$byid"
    return 0
  fi
  while read -r name serial; do
    compact=${serial//-/}
    compact=${compact// /}
    if [[ "$compact" == "$want" ]]; then
      echo "/dev/${name}"
      return 0
    fi
  done < <(lsblk -dn -o NAME,SERIAL)
  return 1
}

echo "waiting for data volume ${DATA_VOLUME_ID}"
dev=""
for _ in $(seq 1 120); do
  if dev=$(find_data_device); then
    break
  fi
  dev=""
  sleep 5
done
if [[ -z "$dev" ]]; then
  echo "data volume ${DATA_VOLUME_ID} did not appear" >&2
  exit 1
fi
echo "data device ${dev}"

if ! blkid "$dev" >/dev/null 2>&1; then
  mkfs.ext4 -F -L ai-models "$dev"
fi
if ! grep -q 'LABEL=ai-models' /etc/fstab; then
  echo 'LABEL=ai-models /data ext4 defaults,nofail,x-systemd.device-timeout=30 0 2' >> /etc/fstab
fi
mkdir -p /data
if ! mountpoint -q /data; then
  mount /data
fi

if [[ ! -f /data/swapfile ]]; then
  fallocate -l 8G /data/swapfile
  chmod 600 /data/swapfile
  mkswap /data/swapfile
fi
if ! grep -q '/data/swapfile' /etc/fstab; then
  echo '/data/swapfile none swap sw,nofail 0 0' >> /etc/fstab
fi
swapon /data/swapfile || true

install -d /usr/local/share/ai-image-studio
aws s3 sync "s3://${BUCKET}/bootstrap/" /usr/local/share/ai-image-studio/

python3 - <<'PY'
import json
from pathlib import Path
manifest = json.loads(Path("/usr/local/share/ai-image-studio/models.manifest.json").read_text(encoding="utf-8"))
node = manifest["custom_nodes"][0]
values = {
    "COMFY_URL": manifest["comfyui"]["git_url"],
    "COMFY_REF": manifest["comfyui"]["ref"],
    "PYTORCH_INDEX": manifest["pytorch_index_url"],
    "NODE_URL": node["git_url"],
    "NODE_REF": node["ref"],
    "NODE_NAME": node["name"],
}
lines = []
for key, value in values.items():
    quoted = "'" + value.replace("'", "'\\''") + "'"
    lines.append(f"{key}={quoted}")
Path("/tmp/ai-image-studio-manifest.env").write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
# shellcheck disable=SC1091
source /tmp/ai-image-studio-manifest.env

if [[ -d /data/ComfyUI && ! -d /data/ComfyUI/.git ]]; then
  rm -rf /data/ComfyUI
fi
if [[ ! -d /data/ComfyUI/.git ]]; then
  git clone --depth 1 --branch "$COMFY_REF" "$COMFY_URL" /data/ComfyUI
fi
if [[ ! -x /data/ComfyUI/venv/bin/python ]]; then
  python3 -m venv /data/ComfyUI/venv
fi

marker=/data/ComfyUI/.deps-stamp
stamp="${COMFY_REF}|${PYTORCH_INDEX}"
current=$(cat "$marker" 2>/dev/null || true)
if [[ "$current" != "$stamp" ]]; then
  /data/ComfyUI/venv/bin/pip install --upgrade pip
  /data/ComfyUI/venv/bin/pip install torch torchvision --index-url "$PYTORCH_INDEX"
  /data/ComfyUI/venv/bin/pip install -r /data/ComfyUI/requirements.txt
  /data/ComfyUI/venv/bin/pip install torch torchvision --index-url "$PYTORCH_INDEX"
  printf '%s\n' "$stamp" > "$marker"
fi

node_dir="/data/ComfyUI/custom_nodes/${NODE_NAME}"
if [[ ! -d "${node_dir}/.git" ]]; then
  rm -rf "$node_dir"
  git clone "$NODE_URL" "$node_dir"
  git -C "$node_dir" checkout "$NODE_REF"
fi
if [[ -f "${node_dir}/requirements.txt" ]]; then
  /data/ComfyUI/venv/bin/pip install -r "${node_dir}/requirements.txt" \
    || echo "warning: custom node requirements failed; basic IP-Adapter may still load" >&2
fi

python3 /usr/local/share/ai-image-studio/download_models.py \
  --manifest /usr/local/share/ai-image-studio/models.manifest.json \
  --dest /data/ComfyUI \
  --region "$AWS_REGION"

mkdir -p /data/ComfyUI/output /data/ComfyUI/input /data/ComfyUI/models/ipadapter /data/ComfyUI/models/clip_vision
chown -R ubuntu:ubuntu /data/ComfyUI
touch /var/log/comfyui.log
chown ubuntu:ubuntu /var/log/comfyui.log

install -m 0755 /usr/local/share/ai-image-studio/comfyui-start.sh /usr/local/sbin/comfyui-start
install -m 0755 /usr/local/share/ai-image-studio/idle-watch.sh /usr/local/sbin/ai-image-studio-idle-watch
install -m 0644 /usr/local/share/ai-image-studio/comfyui.service /etc/systemd/system/comfyui.service
install -m 0644 /usr/local/share/ai-image-studio/idle-watch.service /etc/systemd/system/ai-image-studio-idle.service
install -m 0644 /usr/local/share/ai-image-studio/idle-watch.timer /etc/systemd/system/ai-image-studio-idle.timer
systemctl daemon-reload
systemctl enable comfyui.service
mkdir -p /var/lib/ai-image-studio
touch /var/lib/ai-image-studio/setup-complete
systemctl enable --now ai-image-studio-idle.timer
systemctl disable --now ai-image-studio-failsafe.timer || true
trap - ERR

if [[ "$NO_STOP" -eq 1 ]]; then
  systemctl restart comfyui.service
  echo "setup complete; ComfyUI restarted; instance left running"
else
  echo "setup complete; stopping instance"
  /usr/local/sbin/ai-image-studio-stop || true
fi
