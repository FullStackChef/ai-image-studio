locals {
  bootstrap_files = {
    "bootstrap/setup.sh"             = "${path.module}/../instance/setup.sh"
    "bootstrap/download_models.py"   = "${path.module}/../instance/download_models.py"
    "bootstrap/idle-watch.sh"        = "${path.module}/../instance/idle-watch.sh"
    "bootstrap/comfyui-start.sh"     = "${path.module}/../instance/comfyui-start.sh"
    "bootstrap/comfyui.service"      = "${path.module}/../instance/comfyui.service"
    "bootstrap/idle-watch.service"   = "${path.module}/../instance/idle-watch.service"
    "bootstrap/idle-watch.timer"     = "${path.module}/../instance/idle-watch.timer"
    "bootstrap/models.manifest.json" = "${path.module}/../config/models.manifest.json"
  }
}
