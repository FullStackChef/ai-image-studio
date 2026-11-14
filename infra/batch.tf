resource "aws_ecr_repository" "worker" {
  name                 = "ai-image-studio-worker"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "worker" {
  repository = aws_ecr_repository.worker.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the three most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 3
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ai-image-studio/worker"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "image_build" {
  name              = "/ai-image-studio/image-build"
  retention_in_days = 14
}

resource "aws_launch_template" "gpu" {
  name_prefix = "ai-image-studio-"

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = var.root_volume_gb
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  tag_specifications {
    resource_type = "instance"
    tags = {
      project                = "ai-image-studio"
      "ai-image-studio-role" = "gpu-worker"
    }
  }

  tag_specifications {
    resource_type = "volume"
    tags = {
      project                = "ai-image-studio"
      "ai-image-studio-role" = "gpu-worker"
    }
  }
}

resource "aws_batch_compute_environment" "gpu" {
  compute_environment_name = "ai-image-studio"
  type                     = "MANAGED"
  state                    = "ENABLED"

  compute_resources {
    type                = "EC2"
    allocation_strategy = "BEST_FIT"
    min_vcpus           = 0
    max_vcpus           = var.max_vcpus
    desired_vcpus       = 0
    instance_type       = [var.instance_type]
    instance_role       = aws_iam_instance_profile.gpu.arn
    subnets             = [aws_subnet.public.id]
    security_group_ids  = [aws_security_group.gpu.id]

    ec2_configuration {
      image_type = "ECS_AL2023_NVIDIA"
    }

    launch_template {
      launch_template_id = aws_launch_template.gpu.id
      version            = aws_launch_template.gpu.latest_version
    }

    tags = {
      project                = "ai-image-studio"
      "ai-image-studio-role" = "gpu-worker"
    }
  }

  service_role = aws_iam_role.batch_service.arn

  depends_on = [aws_iam_role_policy_attachment.batch_service]
}

resource "aws_batch_job_queue" "gpu" {
  name     = "ai-image-studio"
  state    = "ENABLED"
  priority = 1

  compute_environment_order {
    order               = 1
    compute_environment = aws_batch_compute_environment.gpu.arn
  }

  job_state_time_limit_action {
    action           = "CANCEL"
    max_time_seconds = 1800
    state            = "RUNNABLE"
    reason           = "No GPU capacity within 30 minutes"
  }
}

resource "aws_batch_job_definition" "worker" {
  name = "ai-image-studio-worker"
  type = "container"

  platform_capabilities = ["EC2"]

  retry_strategy {
    attempts = 1
  }

  timeout {
    attempt_duration_seconds = var.job_timeout_minutes * 60
  }

  container_properties = jsonencode({
    image   = "${aws_ecr_repository.worker.repository_url}:${var.image_tag}"
    command = ["python", "/opt/worker/entrypoint.py"]
    resourceRequirements = [
      { type = "VCPU", value = tostring(var.job_vcpus) },
      { type = "MEMORY", value = tostring(var.job_memory_mb) },
      { type = "GPU", value = "1" },
    ]
    jobRoleArn       = aws_iam_role.job.arn
    executionRoleArn = aws_iam_role.execution.arn
    environment = [
      { name = "AWS_REGION", value = var.aws_region },
      { name = "AWS_DEFAULT_REGION", value = var.aws_region },
      { name = "JOB_BUCKET", value = aws_s3_bucket.studio.bucket },
      { name = "COMFY_ROOT", value = "/opt/ComfyUI" },
      { name = "MANIFEST", value = "/opt/worker/models.manifest.json" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.worker.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "worker"
      }
    }
  })
}

resource "aws_codebuild_project" "worker" {
  name          = "ai-image-studio-worker"
  description   = "Build the slim ComfyUI worker image. Model weights are not part of this image."
  service_role  = aws_iam_role.image_build.arn
  build_timeout = 60

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type                = "BUILD_GENERAL1_LARGE"
    image                       = "aws/codebuild/standard:7.0"
    type                        = "LINUX_CONTAINER"
    privileged_mode             = true
    image_pull_credentials_type = "CODEBUILD"

    environment_variable {
      name  = "ECR_REPO"
      value = aws_ecr_repository.worker.repository_url
    }

    environment_variable {
      name  = "IMAGE_TAG"
      value = var.image_tag
    }
  }

  logs_config {
    cloudwatch_logs {
      group_name = aws_cloudwatch_log_group.image_build.name
    }
  }

  source {
    type      = "S3"
    location  = "${aws_s3_bucket.studio.bucket}/builds/source.zip"
    buildspec = file("${path.module}/buildspec.yml")
  }
}
