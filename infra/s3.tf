resource "aws_s3_bucket" "studio" {
  bucket_prefix = "ai-image-studio-"
  force_destroy = true

  tags = {
    Name = "ai-image-studio"
  }
}

resource "aws_s3_bucket_public_access_block" "studio" {
  bucket                  = aws_s3_bucket.studio.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "studio" {
  bucket = aws_s3_bucket.studio.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "studio" {
  bucket = aws_s3_bucket.studio.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.studio.arn,
      "${aws_s3_bucket.studio.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "studio" {
  bucket = aws_s3_bucket.studio.id
  policy = data.aws_iam_policy_document.bucket.json

  depends_on = [aws_s3_bucket_public_access_block.studio]
}

resource "aws_s3_bucket_lifecycle_configuration" "studio" {
  bucket = aws_s3_bucket.studio.id

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "expire-logs"
    status = "Enabled"

    filter {
      prefix = "logs/"
    }

    expiration {
      days = 30
    }
  }

  rule {
    id     = "expire-jobs"
    status = "Enabled"

    filter {
      prefix = "jobs/"
    }

    expiration {
      days = 14
    }
  }

  rule {
    id     = "expire-build-source"
    status = "Enabled"

    filter {
      prefix = "builds/"
    }

    expiration {
      days = 14
    }
  }

  dynamic "rule" {
    for_each = var.output_retention_days > 0 ? [var.output_retention_days] : []

    content {
      id     = "expire-outputs"
      status = "Enabled"

      filter {
        prefix = "outputs/"
      }

      expiration {
        days = rule.value
      }
    }
  }
}

