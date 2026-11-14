data "archive_file" "reconcile" {
  type        = "zip"
  source_file = "${path.module}/../worker/reconcile.py"
  output_path = "${path.module}/.build/reconcile.zip"
}

resource "aws_lambda_function" "reconcile" {
  function_name    = "ai-image-studio-reconcile"
  role             = aws_iam_role.reconcile.arn
  runtime          = "python3.12"
  handler          = "reconcile.handler"
  filename         = data.archive_file.reconcile.output_path
  source_code_hash = data.archive_file.reconcile.output_base64sha256
  timeout          = 60

  environment {
    variables = {
      JOB_QUEUE        = aws_batch_job_queue.gpu.name
      MAX_AGE_MINUTES  = tostring(var.job_timeout_minutes)
      WORKER_TAG_KEY   = "ai-image-studio-role"
      WORKER_TAG_VALUE = "gpu-worker"
    }
  }

  depends_on = [aws_iam_role_policy_attachment.reconcile_logs]
}

resource "aws_cloudwatch_event_rule" "reconcile" {
  name                = "ai-image-studio-reconcile"
  description         = "Safety net for jobs and disks that outlive a one-shot run"
  schedule_expression = "rate(5 minutes)"
}

resource "aws_cloudwatch_event_target" "reconcile" {
  rule = aws_cloudwatch_event_rule.reconcile.name
  arn  = aws_lambda_function.reconcile.arn
}

resource "aws_lambda_permission" "reconcile" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.reconcile.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.reconcile.arn
}
