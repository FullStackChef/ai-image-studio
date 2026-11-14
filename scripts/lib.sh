#!/usr/bin/env bash
# Shared helpers for the operator scripts. Source this; do not execute it.

aws_region() {
  if [[ -n "${AWS_REGION:-}" ]]; then
    printf '%s\n' "$AWS_REGION"
  elif [[ -n "${AWS_DEFAULT_REGION:-}" ]]; then
    printf '%s\n' "$AWS_DEFAULT_REGION"
  else
    local configured
    configured=$(aws configure get region 2>/dev/null || true)
    if [[ -z "$configured" || "$configured" == "None" ]]; then
      echo "Set AWS_REGION or configure a default AWS region." >&2
      return 1
    fi
    printf '%s\n' "$configured"
  fi
}

ssm_param() {
  local name=$1
  local region
  region=$(aws_region)
  aws ssm get-parameter --region "$region" --name "$name" --query Parameter.Value --output text
}

studio_instance_id() {
  if [[ -n "${INSTANCE_ID:-}" ]]; then
    printf '%s\n' "$INSTANCE_ID"
    return
  fi
  ssm_param /ai-image-studio/instance-id
}

studio_bucket() {
  if [[ -n "${BUCKET:-}" ]]; then
    printf '%s\n' "$BUCKET"
    return
  fi
  ssm_param /ai-image-studio/bucket-name
}

# Build terraform -var arguments. Call from the infra directory.
build_tf_args() {
  if [[ -z "${ALERT_EMAIL:-}" && ! -f terraform.tfvars ]]; then
    echo "Set ALERT_EMAIL or create infra/terraform.tfvars from terraform.tfvars.example." >&2
    return 1
  fi
  TF_ARGS=(-input=false -auto-approve)
  if [[ -n "${AWS_REGION:-}" ]]; then
    TF_ARGS+=(-var "aws_region=${AWS_REGION}")
  elif [[ -n "${AWS_DEFAULT_REGION:-}" ]]; then
    TF_ARGS+=(-var "aws_region=${AWS_DEFAULT_REGION}")
  elif [[ ! -f terraform.tfvars ]]; then
    TF_ARGS+=(-var aws_region=us-east-1)
  fi
  if [[ -n "${ALERT_EMAIL:-}" ]]; then
    TF_ARGS+=(-var "alert_email=${ALERT_EMAIL}")
  fi
  if [[ -n "${INSTANCE_TYPE:-}" ]]; then
    TF_ARGS+=(-var "instance_type=${INSTANCE_TYPE}")
  fi
  if [[ -n "${USE_SPOT:-}" ]]; then
    TF_ARGS+=(-var "use_spot=${USE_SPOT}")
  fi
  if [[ -n "${MONTHLY_BUDGET_USD:-}" ]]; then
    TF_ARGS+=(-var "monthly_budget_usd=${MONTHLY_BUDGET_USD}")
  fi
  if [[ -n "${IDLE_MINUTES:-}" ]]; then
    TF_ARGS+=(-var "idle_minutes=${IDLE_MINUTES}")
  fi
  if [[ -n "${DATA_VOLUME_GB:-}" ]]; then
    TF_ARGS+=(-var "data_volume_gb=${DATA_VOLUME_GB}")
  fi
  if [[ -n "${SETUP_FAILSAFE_MINUTES:-}" ]]; then
    TF_ARGS+=(-var "setup_failsafe_minutes=${SETUP_FAILSAFE_MINUTES}")
  fi
  if [[ -n "${OUTPUT_RETENTION_DAYS:-}" ]]; then
    TF_ARGS+=(-var "output_retention_days=${OUTPUT_RETENTION_DAYS}")
  fi
}
