data "aws_ssm_parameter" "gpu_ami" {
  name = var.gpu_ami_ssm_parameter
}

resource "aws_ebs_volume" "models" {
  availability_zone = aws_subnet.public.availability_zone
  size              = var.data_volume_gb
  type              = "gp3"
  encrypted         = true

  tags = {
    Name = "ai-image-studio-models"
  }
}

resource "aws_instance" "gpu" {
  ami                                  = data.aws_ssm_parameter.gpu_ami.value
  instance_type                        = var.instance_type
  subnet_id                            = aws_subnet.public.id
  vpc_security_group_ids               = [aws_security_group.gpu.id]
  iam_instance_profile                 = aws_iam_instance_profile.gpu.name
  associate_public_ip_address          = true
  instance_initiated_shutdown_behavior = "stop"
  user_data = templatefile("${path.module}/templates/user-data.sh.tftpl", {
    bucket           = aws_s3_bucket.studio.bucket
    region           = var.aws_region
    data_volume_id   = aws_ebs_volume.models.id
    idle_minutes     = var.idle_minutes
    failsafe_minutes = var.setup_failsafe_minutes
  })
  user_data_replace_on_change = false

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true

    tags = {
      Name = "ai-image-studio-root"
    }
  }

  depends_on = [
    aws_s3_object.bootstrap,
    aws_iam_role_policy_attachment.ssm,
    aws_iam_role_policy.gpu_access,
  ]

  lifecycle {
    # The parameter always tracks "latest". Ignore it after the first launch so
    # a newer DLAMI does not replace the instance and throw away the root disk.
    ignore_changes = [ami]
  }

  dynamic "instance_market_options" {
    for_each = var.use_spot ? [1] : []

    content {
      market_type = "spot"

      spot_options {
        instance_interruption_behavior = "stop"
        spot_instance_type             = "persistent"
      }
    }
  }

  tags = {
    Name = "ai-image-studio-gpu"
  }
}

resource "aws_volume_attachment" "models" {
  device_name                    = "/dev/sdf"
  volume_id                      = aws_ebs_volume.models.id
  instance_id                    = aws_instance.gpu.id
  stop_instance_before_detaching = true
}
