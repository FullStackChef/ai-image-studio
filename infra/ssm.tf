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

resource "aws_ssm_parameter" "job_queue" {
  name  = "/ai-image-studio/job-queue"
  type  = "String"
  value = aws_batch_job_queue.gpu.name
}

resource "aws_ssm_parameter" "job_definition" {
  name  = "/ai-image-studio/job-definition"
  type  = "String"
  value = aws_batch_job_definition.worker.name
}

resource "aws_ssm_parameter" "image_build_project" {
  name  = "/ai-image-studio/image-build-project"
  type  = "String"
  value = aws_codebuild_project.worker.name
}

resource "aws_ssm_parameter" "hf_token" {
  name        = "/ai-image-studio/hf-token"
  description = "Optional Hugging Face token. scripts/deploy.sh overwrites this; Terraform will not change it again."
  type        = "SecureString"
  value       = "unset"

  lifecycle {
    ignore_changes = [value]
  }
}
