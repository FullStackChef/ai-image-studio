#!/usr/bin/env python3
"""Submit one ComfyUI job to the scale-to-zero Batch queue and download the image."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from runpod_client import get_json, interpret_status, output_key, post_json, require_endpoint_id, run_url, status_url
from workflows import (
    DEFAULT_MANIFEST,
    GenerationRequest,
    apply_defaults,
    build_workflow,
    load_manifest,
    resolve_mode,
    validate_dimensions,
    validate_image,
)


@dataclass
class AwsConfig:
    region: str
    profile: str | None


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def aws_command(cfg: AwsConfig, args: list[str]) -> list[str]:
    command = ["aws"]
    if cfg.profile:
        command.extend(["--profile", cfg.profile])
    if cfg.region:
        command.extend(["--region", cfg.region])
    return command + args


def run_aws(cfg: AwsConfig, args: list[str], check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        aws_command(cfg, args),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SystemExit(f"error: aws {' '.join(args[:2])} failed: {detail}")
    return result


def resolve_region(explicit: str | None) -> str:
    if explicit:
        return explicit
    result = subprocess.run(["aws", "configure", "get", "region"], check=False, capture_output=True, text=True)
    region = result.stdout.strip()
    if region and region != "None":
        return region
    raise SystemExit("error: set --region or AWS_REGION")


def build_request(args: argparse.Namespace) -> GenerationRequest:
    mode = resolve_mode(args.mode, args.input, args.mask, args.reference)
    for path, label in ((args.input, "--input"), (args.mask, "--mask"), (args.reference, "--reference")):
        validate_image(path, label)
    validate_dimensions(args.width, args.height)
    steps, guidance, denoise = apply_defaults(mode, args.steps, args.guidance, args.denoise)
    seed = args.seed if args.seed is not None else secrets.randbelow(2**31 - 1)
    if seed < 0:
        raise ValueError("--seed must be >= 0")
    request = GenerationRequest(
        mode=mode,
        prompt=args.prompt,
        negative=args.negative,
        width=args.width,
        height=args.height,
        steps=steps,
        guidance=guidance,
        denoise=denoise,
        seed=seed,
        filename_prefix="studio",
        sampler=args.sampler or "euler",
        scheduler=args.scheduler or ("simple" if mode != "reference" else "normal"),
        ip_weight=args.ip_weight,
    )
    if mode in {"img2img", "inpaint"} and args.input is not None:
        request.init_name = f"input{args.input.suffix.lower()}"
    if mode == "inpaint" and args.mask is not None:
        request.mask_name = f"mask{args.mask.suffix.lower()}"
    if mode == "reference" and args.reference is not None:
        request.reference_name = f"reference{args.reference.suffix.lower()}"
    return request


def job_files(request: GenerationRequest, args: argparse.Namespace, job_id: str) -> list[dict[str, str]]:
    files = []
    if request.init_name and args.input is not None:
        files.append({"name": request.init_name, "key": f"jobs/{job_id}/{request.init_name}", "path": args.input})
    if request.mask_name and args.mask is not None:
        files.append({"name": request.mask_name, "key": f"jobs/{job_id}/{request.mask_name}", "path": args.mask})
    if request.reference_name and args.reference is not None:
        files.append(
            {"name": request.reference_name, "key": f"jobs/{job_id}/{request.reference_name}", "path": args.reference}
        )
    return files


def cmd_run(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    request = build_request(args)
    workflow, save_node = build_workflow(request, manifest)
    job_id = "studio-" + secrets.token_hex(8)
    files = job_files(request, args, job_id)
    payload = {
        "job_id": job_id,
        "mode": request.mode,
        "seed": request.seed,
        "steps": request.steps,
        "guidance": request.guidance,
        "denoise": request.denoise,
        "save_node": save_node,
        "workflow": workflow,
        "files": [{"name": item["name"], "key": item["key"]} for item in files],
    }
    if args.dry_run:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    api_key = os.environ.get("RUNPOD_API_KEY", "")
    if not api_key:
        raise SystemExit("error: set RUNPOD_API_KEY")
    endpoint_id = require_endpoint_id(args.endpoint or os.environ.get("RUNPOD_ENDPOINT_ID", ""))
    cfg = AwsConfig(resolve_region(args.region), args.profile)
    bucket = args.bucket or os.environ.get("S3_BUCKET")
    if not bucket:
        raise SystemExit("error: set S3_BUCKET or pass --bucket (terraform output bucket_name)")
    for item in files:
        run_aws(cfg, ["s3", "cp", str(item["path"]), f"s3://{bucket}/{item['key']}", "--sse", "AES256"])
    request_json = json.dumps(
        {"workflow": workflow, "save_node": save_node, "files": payload["files"], "mode": request.mode}
    )
    upload = subprocess.run(
        aws_command(cfg, ["s3", "cp", "-", f"s3://{bucket}/jobs/{job_id}/request.json", "--sse", "AES256"]),
        input=request_json,
        text=True,
        check=False,
        capture_output=True,
    )
    if upload.returncode != 0:
        raise SystemExit(f"error: uploading the job request failed: {(upload.stderr or '').strip()}")

    submitted = post_json(run_url(endpoint_id), {"input": {"bucket": bucket, "job_id": job_id}}, api_key)
    runpod_id = submitted.get("id")
    if not runpod_id:
        raise SystemExit(f"error: RunPod did not return a job id: {submitted}")
    eprint(f"submitted {job_id} ({runpod_id})")
    finished = wait_for_runpod(endpoint_id, str(runpod_id), api_key, args.timeout)
    key = output_key(finished)
    output = args.output or Path("outputs") / f"{job_id}.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    run_aws(cfg, ["s3", "cp", f"s3://{bucket}/{key}", str(output)])
    print(f"local: {output}")
    print(f"s3: s3://{bucket}/{key}")
    return 0


def wait_for_runpod(endpoint_id: str, runpod_id: str, api_key: str, timeout: int) -> dict:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        body = get_json(status_url(endpoint_id, runpod_id), api_key)
        status, output, error = interpret_status(body)
        if status != last:
            eprint(f"job status {status}")
            last = status
        if status == "COMPLETED":
            if output is None:
                raise SystemExit("error: RunPod completed without an output_key")
            return output
        if error or status in {"FAILED", "CANCELLED", "TIMED_OUT"}:
            raise SystemExit(f"error: RunPod job {status}: {error}")
        time.sleep(3)
    raise SystemExit("error: timed out waiting for the RunPod job")


def add_aws_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--region", default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"))
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    parser.add_argument("--bucket", default=os.environ.get("S3_BUCKET"))
    parser.add_argument("--endpoint", default=os.environ.get("RUNPOD_ENDPOINT_ID"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit a one-shot image job to ai-image-studio.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Upload inputs, run one GPU job, and download the image.")
    add_aws_args(run)
    run.add_argument("--prompt", required=True)
    run.add_argument("--negative", default="")
    run.add_argument("--input", type=Path, help="Photo to edit. Omit for text-to-image.")
    run.add_argument("--mask", type=Path, help="White pixels are regenerated. Requires --input.")
    run.add_argument("--reference", type=Path, help="Reference photo for IP-Adapter. Do not combine with --input.")
    run.add_argument("--mode", choices=["auto", "txt2img", "img2img", "inpaint", "reference"], default="auto")
    run.add_argument("--width", type=int, default=1024)
    run.add_argument("--height", type=int, default=1024)
    run.add_argument("--steps", type=int)
    run.add_argument("--guidance", type=float)
    run.add_argument("--denoise", type=float)
    run.add_argument("--seed", type=int)
    run.add_argument("--sampler", default=None)
    run.add_argument("--scheduler", default=None)
    run.add_argument("--ip-weight", type=float, default=0.8)
    run.add_argument("--output", type=Path)
    run.add_argument("--timeout", type=int, default=10800)
    run.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    run.add_argument("--dry-run", action="store_true", help="Print the job JSON and do not call AWS or RunPod.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return cmd_run(args)
    except ValueError as exc:
        eprint(f"error: {exc}")
        return 2
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
