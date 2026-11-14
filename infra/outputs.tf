output "bucket_name" {
  description = "Private bucket for job inputs and generated images. Set this as S3_BUCKET."
  value       = aws_s3_bucket.studio.bucket
}

output "region" {
  value = var.aws_region
}

output "iam_user" {
  description = "User limited to the studio bucket. Create an access key in the IAM console and export it. Terraform does not create keys."
  value       = aws_iam_user.studio.name
}

output "iam_policy_arn" {
  value = aws_iam_policy.studio_s3.arn
}
