resource "aws_ssm_parameter" "instance_id" {
  name  = "/ai-image-studio/instance-id"
  type  = "String"
  value = aws_instance.gpu.id
}

resource "aws_ssm_parameter" "bucket_name" {
  name  = "/ai-image-studio/bucket-name"
  type  = "String"
  value = aws_s3_bucket.studio.bucket
}

resource "aws_ssm_parameter" "region" {
  name  = "/ai-image-studio/region"
  type  = "String"
  value = var.aws_region
}

resource "aws_ssm_parameter" "idle_minutes" {
  name  = "/ai-image-studio/idle-minutes"
  type  = "String"
  value = tostring(var.idle_minutes)
}

resource "aws_ssm_parameter" "hf_token" {
  name        = "/ai-image-studio/hf-token"
  description = "Optional Hugging Face token used only on the instance. scripts/deploy.sh overwrites this; Terraform will not change it again."
  type        = "SecureString"
  value       = "unset"

  lifecycle {
    ignore_changes = [value]
  }
}
