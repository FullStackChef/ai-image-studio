output "instance_id" {
  description = "GPU instance. It is stopped after first-boot setup and after idle periods."
  value       = aws_instance.gpu.id
}

output "bucket_name" {
  description = "Private bucket for bootstrap files, logs, and generated images."
  value       = aws_s3_bucket.studio.bucket
}

output "vpc_id" {
  description = "VPC dedicated to this project."
  value       = aws_vpc.main.id
}

output "region" {
  value = var.aws_region
}

output "data_volume_id" {
  description = "Encrypted volume that caches ComfyUI and model weights."
  value       = aws_ebs_volume.models.id
}

output "port_forward_command" {
  description = "Tunnel localhost:8188 to ComfyUI. The process holds the tunnel open."
  value       = "aws ssm start-session --region ${var.aws_region} --target ${aws_instance.gpu.id} --document-name AWS-StartPortForwardingSession --parameters portNumber=8188,localPortNumber=8188"
}
