variable "aws_region" {
  type        = string
  description = "Region for this stack. Sydney is the default because the owner is in New Zealand."
  default     = "ap-southeast-2"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]+$", var.aws_region))
    error_message = "aws_region must look like ap-southeast-2."
  }
}

variable "alert_email" {
  type        = string
  description = "Inbox for AWS Budget alerts. AWS sends a confirmation mail that must be accepted before alerts arrive."

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must look like an email address."
  }
}

variable "instance_type" {
  type        = string
  description = "GPU instance type Batch may launch. g6.xlarge is one L4 with 16 GiB of system RAM. Use g6.2xlarge if a job is killed for lack of memory, and raise max_vcpus to match."
  default     = "g6.xlarge"

  validation {
    condition     = length(var.instance_type) > 3 && can(regex("^[a-z0-9]+\\.[a-z0-9]+$", var.instance_type))
    error_message = "instance_type must look like g6.xlarge."
  }
}

variable "max_vcpus" {
  type        = number
  description = "Ceiling for the Batch compute environment. 4 is one g6.xlarge, so two jobs cannot run at once."
  default     = 4

  validation {
    condition     = var.max_vcpus >= 4 && var.max_vcpus <= 32
    error_message = "max_vcpus must be between 4 and 32."
  }
}

variable "job_vcpus" {
  type        = number
  description = "vCPUs reserved by one job. Must fit the instance type."
  default     = 4

  validation {
    condition     = var.job_vcpus >= 1 && var.job_vcpus <= 32
    error_message = "job_vcpus must be between 1 and 32."
  }
}

variable "job_memory_mb" {
  type        = number
  description = "Memory in MiB reserved by one job. Leave headroom for the ECS agent on a 16 GiB g6.xlarge."
  default     = 14336

  validation {
    condition     = var.job_memory_mb >= 4096 && var.job_memory_mb <= 65536
    error_message = "job_memory_mb must be between 4096 and 65536."
  }
}

variable "root_volume_gb" {
  type        = number
  description = "Root disk created with each Batch instance and deleted when that instance is terminated. It only holds the container and a scratch copy of the weights for that job."
  default     = 100

  validation {
    condition     = var.root_volume_gb >= 80 && var.root_volume_gb <= 500
    error_message = "root_volume_gb must be between 80 and 500."
  }
}

variable "job_timeout_minutes" {
  type        = number
  description = "Batch attempt timeout. The reconciler terminates older jobs and worker instances."
  default     = 180

  validation {
    condition     = var.job_timeout_minutes >= 30 && var.job_timeout_minutes <= 720
    error_message = "job_timeout_minutes must be between 30 and 720."
  }
}

variable "monthly_budget_usd" {
  type        = number
  description = "Monthly cost budget, filtered to the project=ai-image-studio tag. Alerts go out at 50%, 80%, 100%, and on the 100% forecast."
  default     = 50

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

variable "image_tag" {
  type        = string
  description = "Mutable ECR tag the job runs. scripts/build-image.sh publishes this tag."
  default     = "worker"

  validation {
    condition     = can(regex("^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$", var.image_tag))
    error_message = "image_tag must be an ECR tag."
  }
}
