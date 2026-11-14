output "bucket_name" {
  description = "Private bucket. Weights live under weights/, results under outputs/."
  value       = aws_s3_bucket.studio.bucket
}

output "vpc_id" {
  description = "VPC dedicated to this project."
  value       = aws_vpc.main.id
}

output "region" {
  value = var.aws_region
}

output "job_queue" {
  description = "Batch queue. It stays at zero instances when no job is running."
  value       = aws_batch_job_queue.gpu.name
}

output "job_definition" {
  value = aws_batch_job_definition.worker.name
}

output "worker_image" {
  description = "ECR image scripts/build-image.sh publishes. It does not contain model weights."
  value       = "${aws_ecr_repository.worker.repository_url}:${var.image_tag}"
}

output "image_build_project" {
  value = aws_codebuild_project.worker.name
}
