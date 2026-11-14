# AI Image Studio

One-shot image jobs. The GPU runs on [RunPod serverless](https://docs.runpod.io/serverless/overview) and scales to zero. Photos and finished PNGs live in a private S3 bucket in the owner's AWS account. Terraform creates that bucket, a budget alarm, and an IAM user that can touch only the bucket. It does not create the RunPod endpoint.

ComfyUI does the work: FLUX.1 schnell for text-to-image, image-to-image, and masked edits, and optional SDXL with IP-Adapter for a reference photo.

## Weights: network volume, not the image

RunPod bills a network volume all the time, and a container disk only while a worker is running. Public rates checked against RunPod's docs and pricing page (November 2026):

| | Idle | Cold start |
| --- | --- | --- |
| Network volume (this stack) | about **$2.80/month** for 40 GB at $0.07/GB | slim image pull, then read weights at roughly 200–400 MB/s |
| Weights baked into the image | about **$0** of RunPod storage while scaled to zero | every cold worker pulls a ~30 GB image before it can load the model |

A baked image is a couple of dollars cheaper per month and slower to start, because a scale-to-zero worker on a new host does not keep that image cached. RunPod's own guidance is to put models on a network volume so workers are not re-downloading them. Container disk is about $0.10/GB/month and is charged only while the worker exists, so a 15 GB disk for a few minutes is a fraction of a cent.

The 24 GB Flex GPU tier (L4, 3090, A5000, or a 24 GB MIG slice) is **$0.75 per hour**, billed per second from worker start until it stops. With minimum workers at 0 and a 5 second idle timeout, idle GPU cost is $0.

A later one-shot is about 3–6 minutes of worker time, roughly **$0.04–$0.08**, plus the volume. Ten such jobs in a month are about **$3.50** all-in ($2.80 volume + GPU + well under $1 of S3). The first seed of the volume is a one-time download on a pod you stop afterwards, not on the serverless GPU.

## What Terraform creates

- S3 bucket, SSE-S3, block public access, TLS required, not versioned. Job inputs expire after 14 days. Outputs stay until you delete them or set `output_retention_days`.
- AWS Budget email at 50%, 80%, 100%, and the 100% forecast, tagged `project=ai-image-studio`. Default limit **$10**. This is AWS spend only. RunPod bills separately.
- IAM user `ai-image-studio` with a policy that can list and read/write only that bucket. Terraform does not create access keys.

There is no VPC, NAT gateway, or GPU instance in AWS.

## Prerequisites

- Terraform 1.5+, AWS CLI v2, Python 3.9+, and Docker if you build the image yourself.
- A RunPod account and API key.
- AWS credentials allowed to create S3, IAM, and Budgets for the deploy. Day-to-day keys are the limited user created by the stack.

## Deploy

### 1. AWS bucket and IAM

```bash
export AWS_REGION=ap-southeast-2
export ALERT_EMAIL=you@example.com
./scripts/deploy.sh
```

Activate the `project` cost allocation tag in Billing, and accept the budget confirmation email.

Create an access key for the IAM user `ai-image-studio` in the IAM console. Export it in the shell. Do not commit it and do not put it in Terraform.

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=ap-southeast-2
export S3_BUCKET="$(cd infra && terraform output -raw bucket_name)"
```

Use the same variables on the RunPod endpoint (below), except `S3_BUCKET` is passed per job by the CLI. The endpoint needs the access key, secret, and region so the worker can read the photo and write the PNG.

### 2. Worker image

```bash
./scripts/build-image.sh
```

That only builds a local image tagged `ai-image-studio-worker`. Push it to the registry your RunPod account uses (Docker Hub or GHCR), then select that tag on the endpoint. Rebuild the same way after a ComfyUI pin change. The image does not contain weights.

### 3. Network volume and one-time seed

In the RunPod console, create a **40 GB** network volume in the datacenter where the endpoint will run. Attach it to a temporary pod that uses this image, set `HF_TOKEN` if a download is gated, and run:

```bash
python /opt/worker/seed_weights.py
```

Stop and terminate that pod when the files are under `/runpod-volume/models`. Storage billing continues; GPU billing stops. The optional SDXL and IP-Adapter files need the token and a license acceptance on Hugging Face. Without the token those files are skipped and `--reference` fails until you seed them.

### 4. Serverless endpoint

Create a serverless endpoint with:

| Setting | Value |
| --- | --- |
| Image | the tag you pushed |
| GPU | 24 GB Flex tier (L4 / 3090 / A5000), $0.75/hour |
| Min workers | **0** |
| Max workers | **1** |
| Idle timeout | **5 seconds** |
| Container disk | 15 GB is enough |
| Network volume | the volume from step 3, mounted at `/runpod-volume` |
| Environment | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` |

Do not put `RUNPOD_API_KEY` on the worker. That key stays on your laptop.

```bash
export RUNPOD_API_KEY=...
export RUNPOD_ENDPOINT_ID=...
```

## Run a job

```bash
python3 cli/studio.py run --input photo.jpg --prompt "same subject, evening light"

python3 cli/studio.py run \
  --input photo.jpg --mask mask.png \
  --prompt "replace the background with a plain studio sweep"

python3 cli/studio.py run --prompt "a red ceramic teapot on a wooden table, soft daylight"

python3 cli/studio.py run \
  --reference photo.jpg \
  --prompt "watercolor illustration of the subject"
```

`./scripts/run.sh` is the same command. `--dry-run` prints the workflow and does not call AWS or RunPod.

The client uploads the photo to `s3://$S3_BUCKET/jobs/<id>/`, submits `{bucket, job_id}` to the endpoint, polls until the worker finishes, and downloads `outputs/<id>.png`. The worker links `/runpod-volume/models` over ComfyUI's model folder, runs the workflow on `127.0.0.1`, and writes the PNG back to the bucket. When the queue is empty the worker exits and RunPod scales it to zero.

White pixels in a mask are regenerated. Do not pass `--reference` and `--input` together.

## Models

| Id | Role | License | Required |
| --- | --- | --- | --- |
| `flux_unet` | FLUX.1 schnell fp8 | Apache-2.0 | yes |
| `flux_clip_l`, `flux_t5` | text encoders | Apache-2.0 | yes |
| `flux_vae` | Flux autoencoder | Apache-2.0 | yes |
| `sdxl_base` | SDXL 1.0, for `--reference` | OpenRAIL++ | no |
| `ip_adapter_sdxl` | IP-Adapter Plus SDXL | Apache-2.0 | no |
| `clip_vision_vit_h` | CLIP vision encoder | see the h94/IP-Adapter model card | no |

## Teardown

RunPod resources are deleted in the RunPod console (endpoint first, then the network volume). Terraform does not see them.

```bash
CONFIRM_DESTROY=yes ALERT_EMAIL=you@example.com AWS_REGION=ap-southeast-2 ./scripts/destroy.sh
```

That deletes the bucket, including images, the budget, and the IAM user. Copy `outputs/` out first if you want to keep them.

## Validate

No AWS calls and no RunPod calls:

```bash
./scripts/validate.sh
```

## Layout

| Path | Purpose |
| --- | --- |
| `infra/` | S3 bucket, budget, IAM user and policy. |
| `worker/Dockerfile` | Slim image. No weights. |
| `worker/handler.py` | RunPod handler. |
| `worker/seed_weights.py` | One-time download onto the network volume. |
| `cli/studio.py` | `run`. |
| `scripts/build-image.sh` | Local image build. Does not push. |
