# AI Image Studio

One-shot image jobs in the owner's AWS account. A photo and a prompt go in, a PNG comes back in a private bucket, and the GPU machine is terminated. Nothing stays running between jobs.

ComfyUI does the work: FLUX.1 schnell for text-to-image, image-to-image, and masked edits, and optional SDXL with IP-Adapter when a reference photo is passed. The stack is Terraform, tagged `project=ai-image-studio`, in its own VPC.

## Why the weights are not baked into an image

Each job boots a new machine with a new disk. A custom AMI would store the weights in an EBS snapshot, about **$0.05 per GB-month in Sydney**. A 60–120 GB snapshot (drivers, PyTorch, ComfyUI, and the ~23 GB FLUX set) is about **$3–$6 a month**, and the new disk still has to read those blocks from snapshot storage on every boot. That first read is not faster than copying the same bytes from S3.

Fast Snapshot Restore would make that first read fast. It is billed per snapshot per Availability Zone for every hour it is enabled, on the order of **$0.75–$1 per hour**. Left on for sporadic jobs that is several hundred dollars a month. Do not enable it.

Baking the weights into the container has the same shape. ECR is about **$0.10 per GB-month**, so a ~35 GB image is about **$3.50 a month**, and every job must pull the whole image before ComfyUI can start.

The default is a slim worker image (ComfyUI and its Python dependencies only) plus the weights in the private S3 bucket:

| | Idle per month | Cold start |
| --- | ---: | --- |
| Slim image + S3 weights (this stack) | about $1.50–$2.50 | about 12–20 minutes |
| Weights baked into an AMI, no fast restore | about $3–$6 | about the same, sometimes slower |
| Fast Snapshot Restore left on | hundreds of dollars | a few minutes |
| Weights baked into the container | about $3.50 plus the base image | a single large pull, not faster |

S3 Standard in Sydney is about **$0.025 per GB-month**. Twenty-five gigabytes of weights is under a dollar. The slim image in ECR is roughly 8–12 GB, about **$0.80–$1.20**. There is no NAT gateway, no idle EBS volume, and no instance between jobs.

## Architecture

```text
studio.py run
  |  upload photo + workflow JSON
  v
private S3 (SSE-S3, no public access, not versioned)
  weights/   jobs/<id>/   outputs/<id>.png
  ^
  |  sync weights, run ComfyUI, upload PNG, exit
AWS Batch  (minimum 0 vCPUs, one g6.xlarge, disk deleted on terminate)
```

| Piece | What it does |
| --- | --- |
| VPC `10.42.0.0/16` | One public subnet, no NAT. An S3 gateway endpoint is free. |
| Security group | No inbound rules. Egress is 443, 80, and DNS. |
| Network ACL | Return traffic to ephemeral ports only. 22 and 8188 are not allowed in. |
| Batch | Managed EC2 compute environment, `g6.xlarge`, minimum and desired vCPUs 0, maximum 4. One job at a time. |
| Root disk | 100 GB encrypted gp3, created with the instance, `deleteOnTermination`. Not billed while the queue is empty. |
| Worker image | ComfyUI v0.45.0 and IP-Adapter. No weights. |
| Weights | `s3://<bucket>/weights/`. The first job downloads them from Hugging Face and writes them back. Later jobs only sync from S3. |
| Reconciler | Every five minutes, cancel jobs older than the timeout, terminate worker instances older than that plus 15 minutes, and delete detached worker disks older than 30 minutes. |
| Budget | Email at 50%, 80%, 100%, and the 100% forecast, filtered to `project=ai-image-studio`. |

ComfyUI listens on `127.0.0.1` inside the container. IMDSv2 is required. The instance profile hop limit is 2, which ECS needs.

## Prerequisites

- Terraform 1.5 or newer, AWS CLI v2, and `zip`.
- Credentials that can create the VPC, Batch, ECR, CodeBuild, Lambda, S3, IAM, SSM, and a budget.
- Python 3.9 or newer for the client. It uses the standard library only.
- **G-instance vCPU quota in Sydney (`ap-southeast-2`).** `g6.xlarge` is 4 vCPUs. In Service Quotas, request **Running On-Demand G and VT instances** and, if the account lists it separately, **Running On-Demand G6 instances**. A quota of zero makes the job sit `RUNNABLE` until Batch cancels it after 30 minutes. The increase does not reserve a machine and does not cost anything until a job runs.
- Billing access to activate the `project` cost allocation tag and to confirm the budget email.

## Deploy

```bash
export AWS_REGION=ap-southeast-2
export ALERT_EMAIL=you@example.com
# Optional. Needed only if a required download is rejected, or for the gated SDXL checkpoint.
# export HF_TOKEN=hf_...
./scripts/deploy.sh
./scripts/build-image.sh
```

`./scripts/build-image.sh` is the one-command image build and rebuild. It zips the worker, uploads it, and waits for CodeBuild to push `ai-image-studio-worker:worker`. Run it again after changing the Dockerfile or the pinned ComfyUI revision. It does not download or store model weights.

Copy `infra/terraform.tfvars.example` to `infra/terraform.tfvars` if you prefer a file over environment variables. Optional overrides: `INSTANCE_TYPE`, `MAX_VCPUS`, `JOB_TIMEOUT_MINUTES`, `ROOT_VOLUME_GB`, `MONTHLY_BUDGET_USD`, `OUTPUT_RETENTION_DAYS`.

After the first deploy:

1. Activate the `project` cost allocation tag in Billing. Until it is active, the budget cannot see this stack. The reconciler and the Batch timeout do not depend on it.
2. Accept the AWS Budgets confirmation email.

## Run a job

```bash
python3 cli/studio.py run \
  --input photo.jpg \
  --prompt "same subject, evening light"

# masked edit: white pixels are regenerated, black pixels stay
python3 cli/studio.py run \
  --input photo.jpg \
  --mask mask.png \
  --prompt "replace the background with a plain studio sweep"

# text only
python3 cli/studio.py run --prompt "a red ceramic teapot on a wooden table, soft daylight"

# reference photo (SDXL + IP-Adapter). Do not combine with --input.
python3 cli/studio.py run \
  --reference photo.jpg \
  --prompt "watercolor illustration of the subject"
```

`./scripts/run.sh` is the same command. `--dry-run` prints the workflow and does not call AWS.

The client uploads the photo and the workflow, submits one Batch job, and waits. The worker syncs weights from S3, starts ComfyUI, writes `outputs/<job-id>.png`, and exits. Batch then terminates the instance and its disk. The PNG is also saved under `outputs/` locally.

The first job also fills `weights/` from Hugging Face, so it can take 45–90 minutes. Later jobs are the 12–20 minute cold start: launch, pull the slim image, sync about 23 GB from S3, load the model, generate.

Logs for a failed job are in CloudWatch `/ai-image-studio/worker`. `./scripts/logs.sh` lists the S3 log prefix; worker logs themselves are in CloudWatch.

If a job is killed because the machine runs out of memory, redeploy with `INSTANCE_TYPE=g6.2xlarge MAX_VCPUS=8`. That instance has 32 GiB of RAM. Check the current Sydney price before switching.

## Costs

Prices below are Sydney (`ap-southeast-2`) estimates in USD, before tax. Re-check the [EC2](https://aws.amazon.com/ec2/pricing/on-demand/), [S3](https://aws.amazon.com/s3/pricing/), [ECR](https://aws.amazon.com/ecr/pricing/), and [EBS](https://aws.amazon.com/ebs/pricing/) pages. A previous measurement in this account put `g6.2xlarge` at $1.271 per hour; `g6.xlarge` is the smaller default and is about **$1.05 per hour**.

**Idle, per month**

| Item | Estimate |
| --- | ---: |
| S3 weights, about 25 GB | under $1 |
| ECR slim image, about 8–12 GB | $0.80–$1.20 |
| Logs, job inputs, build zip (they expire) | cents |
| EBS, NAT, public IPv4, GPU | $0 |
| **Typical idle total** | **about $1.50–$2.50** |

**One job**

The instance also pays for a public IPv4 address ($0.005 per hour) and the 100 GB root disk (about $0.013 per hour) only while it exists.

| Job | Time | About |
| --- | --- | ---: |
| Later one-shot (weights already in S3) | 12–20 minutes | $0.25–$0.40 |
| First job, which also downloads the weights | 45–90 minutes | $0.80–$1.70 |

A month with ten ordinary jobs is about **$5–$7** all-in, versus roughly $15–$20 of idle disk on the earlier always-stopped design even if it was never used.

The budget default is $50. It sends email. It does not itself terminate a running job; the Batch timeout (3 hours) and the reconciler do that.

## Models

| Id | Role | License | Required |
| --- | --- | --- | --- |
| `flux_unet` | FLUX.1 schnell fp8 | Apache-2.0 | yes |
| `flux_clip_l`, `flux_t5` | text encoders | Apache-2.0 | yes |
| `flux_vae` | Flux autoencoder | Apache-2.0 | yes |
| `sdxl_base` | SDXL 1.0, for `--reference` | OpenRAIL++ | no |
| `ip_adapter_sdxl` | IP-Adapter Plus SDXL | Apache-2.0 | no |
| `clip_vision_vit_h` | CLIP vision encoder | see the h94/IP-Adapter model card | no |

The stack does not add extra checkpoints. SDXL is gated: accept the license and set `HF_TOKEN` before a reference job, or that download is skipped and the reference job fails until the file is in `weights/`.

## Teardown

Copy out any `outputs/` objects you want to keep. Destroy deletes the bucket, including weights and images.

```bash
CONFIRM_DESTROY=yes ALERT_EMAIL=you@example.com AWS_REGION=ap-southeast-2 ./scripts/destroy.sh
```

The CodeBuild and Batch resources go with the stack. ECR images are removed because the repository is set to force-delete.

## Validate

No AWS calls and no resource creation:

```bash
./scripts/validate.sh
```

That is `terraform fmt -check`, `terraform init -backend=false`, `terraform validate`, the Python unit tests, and shellcheck.

## Layout

| Path | Purpose |
| --- | --- |
| `infra/` | Terraform: network, bucket, Batch, ECR, CodeBuild, reconciler, budget. |
| `worker/Dockerfile` | Slim image. No model weights. |
| `worker/entrypoint.py` | Sync weights, run one workflow, upload the PNG, exit. |
| `worker/reconcile.py` | Orphan jobs, instances, and detached disks. |
| `config/models.manifest.json` | Weight URLs and the pinned filenames. |
| `cli/studio.py` | `run`. |
| `scripts/build-image.sh` | One-command image build and rebuild. |
| `infra/iam-operator-policy.example.json` | Day-to-day IAM. Replace `REGION`, `ACCOUNT`, and `BUCKET`. Deploy itself needs permission to create the stack. |
