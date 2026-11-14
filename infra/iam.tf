data "aws_iam_policy_document" "assume_ec2" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "assume_batch" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["batch.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "assume_ecs_tasks" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "assume_codebuild" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["codebuild.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "assume_lambda" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "batch_service" {
  name_prefix        = "ai-image-studio-batch-"
  assume_role_policy = data.aws_iam_policy_document.assume_batch.json
}

resource "aws_iam_role_policy_attachment" "batch_service" {
  role       = aws_iam_role.batch_service.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole"
}

resource "aws_iam_role" "gpu_instance" {
  name_prefix        = "ai-image-studio-ecs-"
  assume_role_policy = data.aws_iam_policy_document.assume_ec2.json
}

resource "aws_iam_role_policy_attachment" "gpu_instance" {
  role       = aws_iam_role.gpu_instance.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_instance_profile" "gpu" {
  name_prefix = "ai-image-studio-"
  role        = aws_iam_role.gpu_instance.name
}

resource "aws_iam_role" "execution" {
  name_prefix        = "ai-image-studio-exec-"
  assume_role_policy = data.aws_iam_policy_document.assume_ecs_tasks.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "job" {
  name_prefix        = "ai-image-studio-job-"
  assume_role_policy = data.aws_iam_policy_document.assume_ecs_tasks.json
}

data "aws_iam_policy_document" "job" {
  statement {
    sid       = "ListStudioBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.studio.arn]
  }

  statement {
    sid = "ReadWeightsAndJobs"
    actions = [
      "s3:GetObject",
    ]
    resources = [
      "${aws_s3_bucket.studio.arn}/weights/*",
      "${aws_s3_bucket.studio.arn}/jobs/*",
    ]
  }

  statement {
    sid = "WriteWeightsJobsAndOutputs"
    actions = [
      "s3:PutObject",
      "s3:AbortMultipartUpload",
    ]
    resources = [
      "${aws_s3_bucket.studio.arn}/weights/*",
      "${aws_s3_bucket.studio.arn}/jobs/*",
      "${aws_s3_bucket.studio.arn}/outputs/*",
    ]
  }

  statement {
    sid       = "ReadHuggingFaceToken"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.hf_token.arn]
  }

  statement {
    sid       = "DecryptTokenViaSsm"
    actions   = ["kms:Decrypt"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "job" {
  name   = "ai-image-studio-job"
  role   = aws_iam_role.job.id
  policy = data.aws_iam_policy_document.job.json
}

resource "aws_iam_role" "image_build" {
  name_prefix        = "ai-image-studio-build-"
  assume_role_policy = data.aws_iam_policy_document.assume_codebuild.json
}

data "aws_iam_policy_document" "image_build" {
  statement {
    sid       = "ReadBuildSource"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${aws_s3_bucket.studio.arn}/builds/*"]
  }

  statement {
    sid       = "WriteBuildLogs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["*"]
  }

  statement {
    sid       = "AuthEcr"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "PushWorkerImage"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.worker.arn]
  }
}

resource "aws_iam_role_policy" "image_build" {
  name   = "ai-image-studio-build"
  role   = aws_iam_role.image_build.id
  policy = data.aws_iam_policy_document.image_build.json
}

resource "aws_iam_role" "reconcile" {
  name_prefix        = "ai-image-studio-reconcile-"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
}

resource "aws_iam_role_policy_attachment" "reconcile_logs" {
  role       = aws_iam_role.reconcile.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "reconcile" {
  statement {
    sid = "StopOldJobs"
    actions = [
      "batch:ListJobs",
      "batch:DescribeJobs",
      "batch:TerminateJob",
      "batch:CancelJob",
    ]
    resources = ["*"]
  }

  statement {
    sid = "DescribeWorkers"
    actions = [
      "ec2:DescribeInstances",
      "ec2:DescribeVolumes",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "TerminateWorkerInstances"
    actions   = ["ec2:TerminateInstances"]
    resources = ["arn:aws:ec2:${var.aws_region}:*:instance/*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/ai-image-studio-role"
      values   = ["gpu-worker"]
    }
  }

  statement {
    sid       = "DeleteDetachedWorkerDisks"
    actions   = ["ec2:DeleteVolume"]
    resources = ["arn:aws:ec2:${var.aws_region}:*:volume/*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/ai-image-studio-role"
      values   = ["gpu-worker"]
    }
  }
}

resource "aws_iam_role_policy" "reconcile" {
  name   = "ai-image-studio-reconcile"
  role   = aws_iam_role.reconcile.id
  policy = data.aws_iam_policy_document.reconcile.json
}
