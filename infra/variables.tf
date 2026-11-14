variable "aws_region" {
  type        = string
  description = "Region for this isolated stack. GPU quota and the DLAMI parameter must exist here."
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]+$", var.aws_region))
    error_message = "aws_region must look like us-east-1."
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
  description = "GPU instance type. g6.xlarge (NVIDIA L4, 24 GiB) is the default. g5.xlarge also fits these models."
  default     = "g6.xlarge"

  validation {
    condition     = length(var.instance_type) > 3 && can(regex("^[a-z0-9]+\\.[a-z0-9]+$", var.instance_type))
    error_message = "instance_type must look like g6.xlarge."
  }
}

variable "use_spot" {
  type        = bool
  description = "Request a persistent Spot instance that stops on interruption. Default is on-demand so a job is not killed mid-generation."
  default     = false
}

variable "data_volume_gb" {
  type        = number
  description = "Encrypted gp3 volume for ComfyUI and cached weights. 120 GiB holds FLUX schnell fp8, optional SDXL, IP-Adapter, and an 8 GiB swap file."
  default     = 120

  validation {
    condition     = var.data_volume_gb >= 80 && var.data_volume_gb <= 2048
    error_message = "data_volume_gb must be between 80 and 2048."
  }
}

variable "idle_minutes" {
  type        = number
  description = "Stop the instance after this many minutes with an empty ComfyUI queue, low GPU use, and no open UI connection."
  default     = 30

  validation {
    condition     = var.idle_minutes >= 5 && var.idle_minutes <= 720
    error_message = "idle_minutes must be between 5 and 720."
  }
}

variable "setup_failsafe_minutes" {
  type        = number
  description = "Stop the instance if first-boot setup is still incomplete after this many minutes."
  default     = 240

  validation {
    condition     = var.setup_failsafe_minutes >= 30 && var.setup_failsafe_minutes <= 720
    error_message = "setup_failsafe_minutes must be between 30 and 720."
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

variable "gpu_ami_ssm_parameter" {
  type        = string
  description = "Public SSM parameter that resolves to the Deep Learning Base OSS NVIDIA GPU AMI (Ubuntu 22.04) at apply time."
  default     = "/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
}
