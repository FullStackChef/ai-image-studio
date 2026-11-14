#!/usr/bin/env bash
# Syntax and unit checks. This does not call AWS and does not create resources.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

cd "$ROOT/infra"
terraform fmt -check -recursive
terraform init -backend=false -input=false
terraform validate

cd "$ROOT"
python3 -m unittest discover -s cli/tests -v

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -x scripts/*.sh
else
  echo "shellcheck is not installed; skipped shell lint" >&2
fi
