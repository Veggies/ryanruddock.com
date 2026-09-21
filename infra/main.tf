# ---------------------------------------------------------------------------
# Guestbook + visitor counter for ryanruddock.com
#
# Deliberately no API Gateway: a Lambda function URL is a first-class HTTPS
# endpoint at no charge beyond the invocation itself, which removes API
# Gateway's per-request cost from a workload that is otherwise free.
# ---------------------------------------------------------------------------

locals {
  tags = {
    Project   = "ryanruddock.com"
    Component = "guestbook"
    ManagedBy = "terraform"
  }
}

# ----------------------------- storage -------------------------------------

resource "aws_dynamodb_table" "site" {
  name         = var.name
  billing_mode = "PAY_PER_REQUEST" # no provisioned capacity to overrun
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  # visitor-dedupe records expire on their own; entries and the counter
  # carry no ttl attribute and are therefore never swept
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = local.tags
}

# ------------------------------ iam ----------------------------------------

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "fn" {
  name               = "${var.name}-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "fn" {
  statement {
    sid    = "Logs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = ["${aws_cloudwatch_log_group.fn.arn}:*"]
  }

  statement {
    sid    = "TableAccess"
    effect = "Allow"

    # only what the handler actually calls, on only this table
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
    ]

    resources = [aws_dynamodb_table.site.arn]
  }
}

resource "aws_iam_role_policy" "fn" {
  name   = "${var.name}-policy"
  role   = aws_iam_role.fn.id
  policy = data.aws_iam_policy_document.fn.json
}

# ---------------------------- function -------------------------------------

resource "aws_cloudwatch_log_group" "fn" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

data "archive_file" "fn" {
  type        = "zip"
  source_dir  = "${path.module}/lambda"
  output_path = "${path.module}/.terraform/${var.name}.zip"
}

resource "aws_lambda_function" "fn" {
  function_name = var.name
  role          = aws_iam_role.fn.arn
  handler       = "handler.handler"
  runtime       = "python3.12"
  architectures = ["arm64"] # cheaper per GB-second than x86_64
  memory_size   = 128
  timeout       = 10

  filename         = data.archive_file.fn.output_path
  source_code_hash = data.archive_file.fn.output_base64sha256

  reserved_concurrent_executions = var.max_concurrency

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.site.name
    }
  }

  depends_on = [
    aws_iam_role_policy.fn,
    aws_cloudwatch_log_group.fn,
  ]

  tags = local.tags
}

resource "aws_lambda_function_url" "fn" {
  function_name      = aws_lambda_function.fn.function_name
  authorization_type = "NONE" # a public guestbook is public by definition

  cors {
    allow_origins = var.allowed_origins
    allow_methods = ["GET", "POST"]
    allow_headers = ["content-type"]
    max_age       = 3600
  }
}
