variable "region" {
  description = "AWS region. Must match the AWS_REGION used by the site's deploy workflow."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Name prefix for every resource this stack creates."
  type        = string
  default     = "ryanruddock-guestbook"
}

variable "allowed_origins" {
  description = "Origins permitted to call the function URL from a browser."
  type        = list(string)
  default     = ["https://ryanruddock.com", "https://www.ryanruddock.com"]
}

variable "max_concurrency" {
  description = <<-EOT
    Reserved concurrency for the guestbook function, or -1 for no reservation.

    -1 is the default because this account's Lambda limit is 10 concurrent
    executions, and AWS refuses any reservation that would drop unreserved
    concurrency below 10 -- so nothing between 1 and 10 is accepted here. The
    account limit of 10 therefore acts as the outer ceiling, and the killswitch
    (which sets 0, and is allowed) is the fast stop.

    Raise the account quota to 1000 via Service Quotas and this can become a
    real per-function cap such as 2.
  EOT
  type        = number
  default     = -1
}

variable "log_retention_days" {
  description = "CloudWatch log retention for the function."
  type        = number
  default     = 14
}

variable "alert_email" {
  description = "Address to notify when the killswitch trips or the budget is breached. Empty disables email."
  type        = string
  default     = ""
}

variable "flood_threshold" {
  description = <<-EOT
    Invocations in a 5-minute window that count as abuse. At max_concurrency=2
    a flat-out attacker manages roughly 6,000, while a busy day for a personal
    site is a few dozen -- so this sits far above normal and well below the cap.
  EOT
  type        = number
  default     = 1000
}

variable "budget_amount" {
  description = "Monthly budget in USD. Backstop only; the invocation alarm is the fast guardrail."
  type        = string
  default     = "5"
}
