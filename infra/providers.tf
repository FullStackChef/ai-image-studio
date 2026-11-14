provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      project    = "ai-image-studio"
      managed-by = "terraform"
    }
  }
}
