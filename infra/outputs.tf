output "api_base_url" {
  description = "Paste this into the API_BASE constant in index.html (no trailing slash)."
  value       = trimsuffix(aws_lambda_function_url.fn.function_url, "/")
}

output "table_name" {
  description = "DynamoDB table backing the counter and guestbook."
  value       = aws_dynamodb_table.site.name
}

output "log_group" {
  description = "Where the function logs."
  value       = aws_cloudwatch_log_group.fn.name
}

output "killswitch_alarm" {
  description = "CloudWatch alarm that trips the killswitch."
  value       = aws_cloudwatch_metric_alarm.flood.alarm_name
}

output "rearm_command" {
  description = "Run this (or terraform apply) to bring the guestbook back after a trip."
  value = var.max_concurrency < 0 ? (
    "aws lambda delete-function-concurrency --function-name ${aws_lambda_function.fn.function_name}"
    ) : (
    "aws lambda put-function-concurrency --function-name ${aws_lambda_function.fn.function_name} --reserved-concurrent-executions ${var.max_concurrency}"
  )
}
