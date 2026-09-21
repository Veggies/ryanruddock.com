# ---------------------------------------------------------------------------
# Cost killswitch.
#
# AWS Budgets is a detective control: its data refreshes at most three times a
# day, so a budget alone would notice an abuse spike hours after the money is
# gone. This trips on invocation *volume* instead, which CloudWatch sees within
# minutes, and stops the function outright by zeroing its reserved concurrency.
#
# The budget below is kept as a slower backstop for spend the volume alarm
# would not catch.
# ---------------------------------------------------------------------------

# ----------------------------- notification --------------------------------

resource "aws_sns_topic" "killswitch" {
  name = "${var.name}-killswitch"
  tags = local.tags
}

resource "aws_sns_topic_subscription" "killswitch" {
  topic_arn = aws_sns_topic.killswitch.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.killswitch.arn
}

resource "aws_sns_topic_subscription" "killswitch_email" {
  count = var.alert_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.killswitch.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ------------------------------- alarm -------------------------------------

resource "aws_cloudwatch_metric_alarm" "flood" {
  alarm_name        = "${var.name}-invocation-flood"
  alarm_description = "Guestbook function is being invoked far above normal. Trips the killswitch."

  namespace   = "AWS/Lambda"
  metric_name = "Invocations"
  statistic   = "Sum"

  dimensions = {
    FunctionName = aws_lambda_function.fn.function_name
  }

  period              = 300 # 5 minutes
  evaluation_periods  = 1
  threshold           = var.flood_threshold
  comparison_operator = "GreaterThanThreshold"

  # a quiet period reports no datapoints; that is not a flood
  treat_missing_data = "notBreaching"

  alarm_actions = [aws_sns_topic.killswitch.arn]
  tags          = local.tags
}

# ---------------------------- killswitch fn --------------------------------

resource "aws_cloudwatch_log_group" "killswitch" {
  name              = "/aws/lambda/${var.name}-killswitch"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_iam_role" "killswitch" {
  name               = "${var.name}-killswitch-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "killswitch" {
  statement {
    sid    = "Logs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = ["${aws_cloudwatch_log_group.killswitch.arn}:*"]
  }

  statement {
    sid    = "ThrottleGuestbookOnly"
    effect = "Allow"

    # it can throttle the guestbook function and nothing else
    actions   = ["lambda:PutFunctionConcurrency"]
    resources = [aws_lambda_function.fn.arn]
  }
}

resource "aws_iam_role_policy" "killswitch" {
  name   = "${var.name}-killswitch-policy"
  role   = aws_iam_role.killswitch.id
  policy = data.aws_iam_policy_document.killswitch.json
}

data "archive_file" "killswitch" {
  type        = "zip"
  source_dir  = "${path.module}/killswitch"
  output_path = "${path.module}/.terraform/${var.name}-killswitch.zip"
}

resource "aws_lambda_function" "killswitch" {
  function_name = "${var.name}-killswitch"
  role          = aws_iam_role.killswitch.arn
  handler       = "killswitch.handler"
  runtime       = "python3.12"
  architectures = ["arm64"]
  memory_size   = 128
  timeout       = 10

  filename         = data.archive_file.killswitch.output_path
  source_code_hash = data.archive_file.killswitch.output_base64sha256

  environment {
    variables = {
      TARGET_FUNCTION = aws_lambda_function.fn.function_name
    }
  }

  depends_on = [
    aws_iam_role_policy.killswitch,
    aws_cloudwatch_log_group.killswitch,
  ]

  tags = local.tags
}

resource "aws_lambda_permission" "killswitch_sns" {
  statement_id  = "AllowSNSInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.killswitch.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.killswitch.arn
}

# ------------------------------- budget ------------------------------------

resource "aws_budgets_budget" "site" {
  name         = "${var.name}-monthly"
  budget_type  = "COST"
  limit_amount = var.budget_amount
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # warn on the way up, and again if actual spend lands
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_sns_topic_arns  = [aws_sns_topic.killswitch.arn]
    subscriber_email_addresses = var.alert_email == "" ? [] : [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_sns_topic_arns  = [aws_sns_topic.killswitch.arn]
    subscriber_email_addresses = var.alert_email == "" ? [] : [var.alert_email]
  }
}

resource "aws_sns_topic_policy" "killswitch" {
  arn    = aws_sns_topic.killswitch.arn
  policy = data.aws_iam_policy_document.killswitch_topic.json
}

data "aws_iam_policy_document" "killswitch_topic" {
  statement {
    sid     = "AllowCloudWatchAlarms"
    effect  = "Allow"
    actions = ["SNS:Publish"]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    resources = [aws_sns_topic.killswitch.arn]
  }

  statement {
    sid     = "AllowBudgets"
    effect  = "Allow"
    actions = ["SNS:Publish"]

    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }

    resources = [aws_sns_topic.killswitch.arn]
  }
}
