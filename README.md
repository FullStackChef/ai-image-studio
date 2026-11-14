# AI Image Studio

Private image generation for one person. A GPU EC2 instance in its own VPC runs [ComfyUI](https://github.com/comfyanonymous/ComfyUI) with open weights you control: FLUX.1 schnell for text-to-image, image-to-image, and masked edits, plus optional SDXL with IP-Adapter when you want a reference photo. Nothing in this stack listens on a public port. The instance is stopped unless you are using it.

This repository is the infrastructure, the instance setup, and a small Python client. It does not deploy anything by itself.

## Why Terraform

The studio is one Terraform root module and one VPC. Terraform talks to AWS with your own credentials at apply time and does not need the shared CDK bootstrap stack (`CDKToolkit` and its asset bucket). `terraform validate` also checks the configuration without creating resources. CDK is a reasonable alternative; it was set aside here so the project stays limited to the resources tagged `project=ai-image-studio`.

## Architecture

```text
your laptop
  |  aws CLI + Session Manager (no inbound ports)
  v
EC2 GPU instance (ComfyUI on 127.0.0.1:8188 only)
  |-- encrypted gp3 data volume: ComfyUI, weights, swap
  |-- idle timer: stop after 30 quiet minutes
  v
S3 bucket (block public access, SSE-S3, TLS required)
  outputs/, logs/, bootstrap/
```

| Piece | What it does |
| --- | --- |
| VPC `10.42.0.0/16` | Dedicated network. No NAT gateway and no interface VPC endpoints, because those bill while the GPU is stopped. |
| Security group | No ingress rules. Egress is 443, 80, and DNS. |
| Network ACL | Allows return traffic to ephemeral ports (32768–65535) only. Ports 22 and 8188 are not in that range. |
| Instance | Deep Learning Base GPU AMI (Ubuntu 22.04, NVIDIA driver included). Public IPv4 is for outbound SSM, package installs, and model downloads. The address is released when the instance stops. IMDSv2 is required. There is no SSH key. |
| Data volume | Encrypted gp3 disk mounted at `/data`. Weights survive stop/start. |
| ComfyUI | Listens on `127.0.0.1:8188`. You reach it with an SSM port-forward. |
| S3 | Private bucket for bootstrap files, setup logs, and images. `force_destroy` is on so teardown can empty it. |
| Budget | Monthly cost budget filtered to tag `project=ai-image-studio`, with email at 50%, 80%, 100%, and the 100% forecast. |
| Idle shutdown | A timer on the instance calls `StopInstances` on itself after the queue is empty, GPU use is under 15%, and nobody has the UI connected. |

Every resource in the stack gets `project=ai-image-studio` and `managed-by=terraform`.

The image stack is stock ComfyUI plus the weights in [`config/models.manifest.json`](config/models.manifest.json). FLUX.1 schnell (Apache-2.0) does text-to-image, image-to-image, and masked inpainting. SDXL (OpenRAIL++) with IP-Adapter is optional and is what `--reference` uses. No extra checkpoints are installed.

## Prerequisites

- Terraform 1.5 or newer and AWS CLI v2, with credentials that can create a VPC, EC2, EBS, S3, IAM, SSM parameters, and a budget.
- [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) on the laptop that runs the client.
- Python 3.9 or newer. The client uses the standard library only.
- GPU quota for the instance type. `g6.xlarge` is 4 vCPUs. Check Service Quotas for **Running On-Demand G and VT instances** and, where the account lists it separately, **Running On-Demand G6 instances**. A failed launch because of quota does not leave the instance running.
- Billing access to activate the `project` cost allocation tag and to confirm the budget email.

Default region in the example config is `us-east-1`. Prices below are us-east-1 Linux on-demand list prices checked in November 2026. Other regions cost more.

## Deploy

```bash
export AWS_REGION=us-east-1
export ALERT_EMAIL=you@example.com
# Optional. Needed for the gated SDXL checkpoint used by --reference.
# Accept the Stability AI license on Hugging Face first.
# export HF_TOKEN=hf_...
./scripts/deploy.sh
```

`ALERT_EMAIL` can also live in `infra/terraform.tfvars` (copy [`infra/terraform.tfvars.example`](infra/terraform.tfvars.example); that file is gitignored). Optional environment overrides: `INSTANCE_TYPE`, `USE_SPOT=true`, `MONTHLY_BUDGET_USD`, `IDLE_MINUTES`, `DATA_VOLUME_GB`, `SETUP_FAILSAFE_MINUTES`, `OUTPUT_RETENTION_DAYS`.

What the first apply does:

1. Creates the VPC, private bucket, budget, and GPU instance.
2. The instance boots once, installs ComfyUI v0.45.0, and caches weights on the encrypted data volume.
3. Setup uploads its log to `s3://<bucket>/logs/` and stops the instance.

EC2 instances cannot be created already stopped. The instance runs for the length of that first boot, then stops itself. Required weights are about 23 GB (FLUX.1 schnell fp8, the T5 and CLIP encoders, and the Flux VAE). Optional SDXL, IP-Adapter, and the CLIP vision encoder add roughly 10 GB and are skipped, with a warning in the setup log, if the download is unauthorized. Plan on roughly 30–90 minutes of GPU time for that first boot (`g6.xlarge` is about $0.40–$1.20), then the instance is stopped.

If setup fails, the instance stops and the log is in the bucket:

```bash
./scripts/logs.sh
aws s3 cp "s3://BUCKET/logs/INSTANCE-ai-image-studio-setup.log" -
```

A four-hour failsafe (`setup_failsafe_minutes`) stops the instance if setup hangs. After you fix the cause (a token, a quota, a full disk), `./scripts/start.sh` runs setup again because the completion marker was not written.

To store a token without printing it, export `HF_TOKEN` and re-run `./scripts/deploy.sh`. The value is written to the SecureString parameter `/ai-image-studio/hf-token`. Terraform creates that parameter as `unset` and will not overwrite a later value. The token is not in the CloudFormation-equivalent template; it can still land in local Terraform state if you later move the value into a variable. This path does not do that. Keep `*.tfstate` private (it is gitignored).

### After deploy, do these two billing steps

1. In Billing → Cost allocation tags, activate `project`. The budget filter is `user:project$ai-image-studio`. Until the tag is active, the budget does not see this stack's spend. Activation can take up to a day. The idle shutdown does not depend on it.
2. Accept the AWS Budgets confirmation email. Alerts are dropped until you do.

## Use

Use the same region you deployed to (`AWS_REGION`).

```bash
./scripts/status.sh
./scripts/start.sh
./scripts/stop.sh
```

`start` waits until the SSM agent is online. ComfyUI still needs a few minutes after that to load weights into the GPU. `stop` stops the instance immediately. The data volume stays attached, so the next start does not re-download models.

Generate from the laptop. The client starts the instance if it is stopped, opens an SSM tunnel to localhost, submits a ComfyUI API job, writes the PNG under `outputs/` (gitignored), and copies it to the private bucket.

```bash
python3 cli/studio.py generate --prompt "a red ceramic teapot on a wooden table, soft daylight"

# image-to-image. denoise defaults to 0.55
python3 cli/studio.py generate \
  --prompt "same subject, evening light" \
  --init photo.png

# masked edit. white pixels in the mask are regenerated; black pixels stay
python3 cli/studio.py generate \
  --prompt "replace the background with a plain studio sweep" \
  --init photo.png \
  --mask mask.png

# reference photo, via SDXL + IP-Adapter. needs the optional weights
python3 cli/studio.py generate \
  --prompt "watercolor illustration of the subject" \
  --reference photo.png

# stop as soon as the file is uploaded, instead of waiting for the idle timer
python3 cli/studio.py generate --prompt "a red ceramic teapot" --stop
```

`./scripts/generate.sh` is the same client. `--dry-run` prints the workflow JSON and does not call AWS.

Defaults: FLUX modes are 4 steps, guidance 3.5, 1024×1024. Reference mode is 30 steps, CFG 7. Width and height must be multiples of 16. `--seed` fixes the seed.

The UI, still only on your machine:

```bash
./scripts/start.sh
./scripts/ui.sh
# http://127.0.0.1:8188 while that command is running
```

A shell for debugging, also with no SSH port:

```bash
./scripts/session.sh
```

An open UI connection counts as activity, so the idle timer does not stop the instance while you are using the page. After you disconnect and the GPU is idle, it stops once `idle_minutes` (default 30) have passed. Change that later with `IDLE_MINUTES=45 ./scripts/deploy.sh`; the instance reads `/ai-image-studio/idle-minutes` on each check.

Re-run setup after you change the manifest or add `HF_TOKEN` (the instance must be running):

```bash
./scripts/session.sh
sudo bash -lc 'set -a; source /etc/ai-image-studio.env; set +a; aws s3 cp "s3://${BUCKET}/bootstrap/setup.sh" /tmp/ai-image-studio-setup.sh && bash /tmp/ai-image-studio-setup.sh --no-stop'
```

## Models

| Id | Role | License | Required |
| --- | --- | --- | --- |
| `flux_unet` | FLUX.1 schnell fp8 | Apache-2.0 | yes |
| `flux_clip_l`, `flux_t5` | text encoders | Apache-2.0 | yes |
| `flux_vae` | Flux autoencoder | Apache-2.0 | yes |
| `sdxl_base` | SDXL 1.0 base, for `--reference` | OpenRAIL++ | no |
| `ip_adapter_sdxl` | IP-Adapter Plus SDXL | Apache-2.0 | no |
| `clip_vision_vit_h` | CLIP vision encoder used by IP-Adapter | see the h94/IP-Adapter model card | no |

The VAE URL list prefers the Comfy-Org copy of the Apache-2.0 Flux autoencoder and falls back to `black-forest-labs/FLUX.1-schnell`, which is gated. SDXL is gated: accept the license on Hugging Face and set `HF_TOKEN`, or reference mode will fail at job time with a missing-checkpoint error from ComfyUI. Pinned versions are ComfyUI `v0.45.0` and `ComfyUI_IPAdapter_plus` commit `a0f451a`. PyTorch wheels come from the CUDA 12.6 index. `g6.xlarge` and `g5.xlarge` have 16 GiB of RAM and 24 GiB of GPU memory; the setup adds an 8 GiB swap file so the fp8 FLUX load has somewhere to spill. If a job is killed, set `INSTANCE_TYPE=g6.2xlarge` (more RAM; check the current price first) or set `COMFY_ARGS=--lowvram` in `/etc/ai-image-studio.env` and restart the service.

Spot is off by default. `USE_SPOT=true` asks for a persistent Spot instance that stops on interruption. That is cheaper when capacity exists and can kill a generation. `g6` Spot interruption rates move around; on-demand is the predictable choice for a single user.

## Costs

Stopped, you pay for disk, not GPU. gp3 in us-east-1 is **$0.08 per GB-month**. The data volume default is 120 GB (**$9.60/month**). The root volume uses the DLAMI snapshot size so Terraform does not shrink it; check the console after the first apply and multiply by $0.08. A root volume around 80 GB is about **$6.40/month**. S3 for logs and images is cents unless you store a lot. The first two AWS Budgets in an account are free; another budget is about $0.02/day.

Public IPv4 is **$0.005/hour while the instance is running** and is released when it stops, so it is $0 while stopped.

| | g6.xlarge (default) | g5.xlarge |
| --- | ---: | ---: |
| GPU | 1× NVIDIA L4, 24 GiB | 1× NVIDIA A10G, 24 GiB |
| On-demand | $0.8048/hour | $1.006/hour |
| 10 hours in a month, plus ~$16 EBS | about $24 | about $26 |
| Left running all month (730 hours) | about $605 plus EBS | about $750 plus EBS |

The 10-hour row is `10 × ($0.8048 + $0.005)` plus an assumed $16 of EBS. A single generation that you then walk away from is a few minutes of compute plus up to 30 idle minutes, on the order of $0.50, because the instance stops itself.

The number to avoid is the always-on row. The idle timer is what prevents it. The $50 budget is the second alarm, and it only tracks this project after the `project` tag is activated. Confirm current prices on the [EC2 pricing page](https://aws.amazon.com/ec2/pricing/on-demand/) before you change instance type or region; eu-central-1 and ap-northeast-1 are higher than us-east-1.

Ingress of the model download is free. Images you download from S3 to your laptop are ordinary internet egress (about $0.09/GB); the files are small.

## Teardown

Copy anything you want to keep out of the bucket first. Destroy deletes the bucket, including generated images, and deletes the model volume.

```bash
CONFIRM_DESTROY=yes ALERT_EMAIL=you@example.com AWS_REGION=us-east-1 ./scripts/destroy.sh
```

## Validate

No AWS credentials and no resource creation:

```bash
./scripts/validate.sh
```

That runs `terraform fmt -check`, `terraform init -backend=false`, `terraform validate`, the Python unit tests, and shellcheck.

## Layout

| Path | Purpose |
| --- | --- |
| `infra/` | Terraform module. |
| `instance/` | Setup, model download, ComfyUI service, idle shutdown. Uploaded to the bucket on apply. |
| `config/models.manifest.json` | Weights, licenses, and the pinned ComfyUI revision. |
| `cli/studio.py` | `generate`, `start`, `stop`, `status`. |
| `scripts/` | `deploy`, `destroy`, `start`, `stop`, `status`, `generate`, `ui`, `session`, `logs`, `validate`. |
| `infra/iam-operator-policy.example.json` | Narrower day-to-day IAM policy. Replace the `REGION`, `ACCOUNT`, `INSTANCE_ID`, and `BUCKET` placeholders. Deploy itself needs permission to create the stack; an administrator principal is the practical choice for that one-time apply. |

## Security notes

- ComfyUI is bound to loopback. The security group has no ingress. The network ACL does not allow inbound 22 or 8188.
- The instance role can stop this instance, read and write this bucket, and read two SSM parameters. It cannot terminate the instance or touch other buckets.
- EBS uses the default `aws/ebs` key. S3 uses SSE-S3. The bucket policy denies non-TLS requests. Public ACLs and policies are blocked, and object ownership is bucket-owner enforced.
- Do not commit `terraform.tfvars`, state files, `HF_TOKEN`, or the `outputs/` directory. `.gitignore` covers those.
- Local state contains resource ids. It does not contain the Hugging Face token when you use `HF_TOKEN` with `scripts/deploy.sh`.
