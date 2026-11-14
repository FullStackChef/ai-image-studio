data "aws_iam_policy_document" "assume_ec2" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "gpu" {
  name_prefix        = "ai-image-studio-"
  assume_role_policy = data.aws_iam_policy_document.assume_ec2.json
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.gpu.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "gpu_access" {
  statement {
    sid       = "ListStudioBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.studio.arn]
  }

  statement {
    sid = "UseStudioObjects"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:AbortMultipartUpload",
    ]
    resources = ["${aws_s3_bucket.studio.arn}/*"]
  }

  statement {
    sid = "ReadStudioParameters"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
    ]
    resources = [
      aws_ssm_parameter.hf_token.arn,
      aws_ssm_parameter.idle_minutes.arn,
    ]
  }

  statement {
    sid       = "DecryptParametersViaSsm"
    actions   = ["kms:Decrypt"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "gpu_access" {
  name   = "ai-image-studio-access"
  role   = aws_iam_role.gpu.id
  policy = data.aws_iam_policy_document.gpu_access.json
}

data "aws_iam_policy_document" "gpu_stop" {
  statement {
    sid       = "StopThisInstance"
    actions   = ["ec2:StopInstances"]
    resources = [aws_instance.gpu.arn]
  }
}

resource "aws_iam_role_policy" "gpu_stop" {
  name   = "ai-image-studio-stop"
  role   = aws_iam_role.gpu.id
  policy = data.aws_iam_policy_document.gpu_stop.json
}

resource "aws_iam_instance_profile" "gpu" {
  name_prefix = "ai-image-studio-"
  role        = aws_iam_role.gpu.name
}
