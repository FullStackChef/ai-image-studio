#!/usr/bin/env bash
# Shell on the instance. There is no SSH port.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"
id=$(studio_instance_id)
region=$(aws_region)
exec aws ssm start-session --region "$region" --target "$id"
