variable "aws_region" {
  type        = string
  description = "Region for the private bucket. Sydney is the default because the owner is in New Zealand. RunPod GPU location is chosen in the RunPod console and does not have to match."
  default     = "ap-southeast-2"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]+$", var.aws_region))
    error_message = "aws_region must look like ap-southeast-2."
  }
}

variable "alert_email" {
  type        = string
  description = "Inbox for AWS Budget alerts. AWS sends a confirmation mail that must be accepted before alerts arrive. This budget covers the bucket only, not RunPod."

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must look like an email address."
  }
}

variable "monthly_budget_usd" {
  type        = number
  description = "Monthly AWS budget for this project. GPU time is billed by RunPod and is not included."
  default     = 10

  validation {
    condition     = var.monthly_budget_usd >= 1 && var.monthly_budget_usd <= 100000
    error_message = "monthly_budget_usd must be between 1 and 100000."
  }
}

variable "output_retention_days" {
  type        = number
  description = "Delete objects under outputs/ after this many days. 0 keeps them until teardown."
  default     = 0

  validation {
    condition     = var.output_retention_days >= 0 && var.output_retention_days <= 3650
    error_message = "output_retention_days must be between 0 and 3650."
  }
}
