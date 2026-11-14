data "aws_iam_policy_document" "studio_s3" {
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
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
    ]
    resources = ["${aws_s3_bucket.studio.arn}/*"]
  }
}

resource "aws_iam_policy" "studio_s3" {
  name_prefix = "ai-image-studio-s3-"
  description = "Read and write only the ai-image-studio bucket."
  policy      = data.aws_iam_policy_document.studio_s3.json
}

resource "aws_iam_user" "studio" {
  name = "ai-image-studio"
}

resource "aws_iam_user_policy_attachment" "studio_s3" {
  user       = aws_iam_user.studio.name
  policy_arn = aws_iam_policy.studio_s3.arn
}
