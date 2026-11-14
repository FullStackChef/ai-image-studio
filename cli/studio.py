#!/usr/bin/env python3
"""Operate the private ComfyUI instance over SSM and download its images."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from comfy_client import (
    encode_multipart,
    image_from_history,
    prompt_id_from_response,
    safe_upload_name,
    view_query,
)
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


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"error: {name} is not on PATH")


def ssm_value(cfg: AwsConfig, name: str) -> str:
    result = run_aws(
        cfg,
        ["ssm", "get-parameter", "--name", name, "--query", "Parameter.Value", "--output", "text"],
    )
    value = result.stdout.strip()
    if not value or value == "None":
        raise SystemExit(f"error: SSM parameter {name} is empty. Deploy the stack first.")
    return value


def instance_state(cfg: AwsConfig, instance_id: str) -> str:
    result = run_aws(
        cfg,
        [
            "ec2",
            "describe-instances",
            "--instance-ids",
            instance_id,
            "--query",
            "Reservations[0].Instances[0].State.Name",
            "--output",
            "text",
        ],
    )
    return result.stdout.strip()


def ensure_running(cfg: AwsConfig, instance_id: str, timeout: int) -> None:
    state = instance_state(cfg, instance_id)
    eprint(f"instance {instance_id} is {state}")
    if state == "stopping":
        run_aws(cfg, ["ec2", "wait", "instance-stopped", "--instance-ids", instance_id], timeout=timeout)
        state = "stopped"
    if state in {"stopped", "stopping"}:
        run_aws(cfg, ["ec2", "start-instances", "--instance-ids", instance_id])
        state = "pending"
    if state in {"pending", "stopped"}:
        eprint("waiting for the instance to reach running")
        run_aws(cfg, ["ec2", "wait", "instance-running", "--instance-ids", instance_id], timeout=timeout)
    elif state != "running":
        raise SystemExit(f"error: instance {instance_id} is {state}")
    wait_for_ssm(cfg, instance_id, timeout)


def wait_for_ssm(cfg: AwsConfig, instance_id: str, timeout: int) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = run_aws(
            cfg,
            [
                "ssm",
                "describe-instance-information",
                "--filters",
                f"Key=InstanceIds,Values={instance_id}",
                "--query",
                "InstanceInformationList[0].PingStatus",
                "--output",
                "text",
            ],
            check=False,
        )
        if result.stdout.strip() == "Online":
            eprint("SSM agent is online")
            return
        time.sleep(10)
    raise SystemExit("error: SSM agent did not come online")


def stop_instance(cfg: AwsConfig, instance_id: str) -> None:
    eprint(f"stopping {instance_id}")
    run_aws(cfg, ["ec2", "stop-instances", "--instance-ids", instance_id])
    run_aws(cfg, ["ec2", "wait", "instance-stopped", "--instance-ids", instance_id], timeout=900)


def pick_port(preferred: int) -> int:
    if preferred:
        return preferred
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def open_tunnel(cfg: AwsConfig, instance_id: str, local_port: int, log_path: Path) -> subprocess.Popen[str]:
    log_handle = log_path.open("w", encoding="utf-8")
    command = aws_command(
        cfg,
        [
            "ssm",
            "start-session",
            "--target",
            instance_id,
            "--document-name",
            "AWS-StartPortForwardingSession",
            "--parameters",
            f"portNumber=8188,localPortNumber={local_port}",
        ],
    )
    return subprocess.Popen(command, stdout=log_handle, stderr=subprocess.STDOUT, text=True)


def wait_for_port(port: int, process: subprocess.Popen[str], log_path: Path, timeout: int) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            detail = log_path.read_text(encoding="utf-8", errors="replace")[-1000:]
            raise SystemExit(f"error: SSM port forward exited.\n{detail}")
        with socket.socket() as sock:
            sock.settimeout(1)
            try:
                sock.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(1)
    raise SystemExit("error: timed out waiting for the SSM port forward")


def http_json(method: str, url: str, payload: dict | None = None, timeout: int = 60) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:800]
        raise SystemExit(f"error: ComfyUI {method} {url} returned {exc.code}: {detail}") from exc


def wait_for_comfy(base: str, timeout: int) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/system_stats", timeout=5) as response:
                if response.status == 200:
                    eprint("ComfyUI is ready")
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(3)
    raise SystemExit(
        "error: ComfyUI did not become ready. On a cold start the weights take a few minutes to load. "
        "Check /var/log/comfyui.log over SSM if this persists."
    )


def upload_image(base: str, path: Path) -> str:
    name = safe_upload_name(path)
    body, content_type = encode_multipart(path, name)
    request = urllib.request.Request(f"{base}/upload/image", data=body, method="POST")
    request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:800]
        raise SystemExit(f"error: upload of {path} failed: {detail}") from exc
    uploaded = payload.get("name") or name
    subfolder = payload.get("subfolder") or ""
    return f"{subfolder}/{uploaded}" if subfolder else uploaded


def poll_image(base: str, prompt_id: str, save_node: str, timeout: int) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        history = http_json("GET", f"{base}/history/{prompt_id}")
        image = image_from_history(history, prompt_id, save_node)
        if image:
            return image
        time.sleep(2)
    raise SystemExit("error: timed out waiting for the ComfyUI job")


def download_view(base: str, image: dict, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(f"{base}/view?{view_query(image)}", timeout=120) as response:
        dest.write_bytes(response.read())


def build_request(args: argparse.Namespace, manifest: dict) -> tuple[GenerationRequest, str]:
    mode = resolve_mode(args.mode, args.init, args.mask, args.reference)
    for path, label in ((args.init, "--init"), (args.mask, "--mask"), (args.reference, "--reference")):
        validate_image(path, label)
    validate_dimensions(args.width, args.height)
    steps, guidance, denoise = apply_defaults(mode, args.steps, args.guidance, args.denoise)
    if args.ip_weight < -1 or args.ip_weight > 5:
        raise ValueError("--ip-weight must be between -1 and 5")
    seed = args.seed if args.seed is not None else secrets.randbelow(2**31 - 1)
    if seed < 0:
        raise ValueError("--seed must be >= 0")
    sampler = args.sampler or "euler"
    scheduler = args.scheduler or ("simple" if mode != "reference" else "normal")
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
        sampler=sampler,
        scheduler=scheduler,
        ip_weight=args.ip_weight,
    )
    return request, mode


def assign_local_names(request: GenerationRequest, args: argparse.Namespace) -> None:
    if request.mode in {"img2img", "inpaint"} and args.init is not None:
        request.init_name = args.init.name
    if request.mode == "inpaint" and args.mask is not None:
        request.mask_name = args.mask.name
    if request.mode == "reference" and args.reference is not None:
        request.reference_name = args.reference.name


def cmd_generate(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    request, _mode = build_request(args, manifest)
    if args.dry_run:
        assign_local_names(request, args)
        workflow, save_node = build_workflow(request, manifest)
        json.dump(
            {
                "mode": request.mode,
                "seed": request.seed,
                "steps": request.steps,
                "guidance": request.guidance,
                "denoise": request.denoise,
                "save_node": save_node,
                "workflow": workflow,
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0

    require_tool("aws")
    require_tool("session-manager-plugin")
    cfg = AwsConfig(resolve_region(args.region), args.profile)
    instance_id = args.instance_id or ssm_value(cfg, "/ai-image-studio/instance-id")
    bucket = args.bucket or ssm_value(cfg, "/ai-image-studio/bucket-name")
    ensure_running(cfg, instance_id, args.boot_timeout)
    local_port = pick_port(args.local_port)
    log_path = Path(tempfile.gettempdir()) / f"ai-image-studio-ssm-{local_port}.log"
    tunnel = open_tunnel(cfg, instance_id, local_port, log_path)
    exit_code = 0
    try:
        wait_for_port(local_port, tunnel, log_path, timeout=60)
        base = f"http://127.0.0.1:{local_port}"
        wait_for_comfy(base, args.ready_timeout)
        if request.mode in {"img2img", "inpaint"}:
            request.init_name = upload_image(base, args.init)
        if request.mode == "inpaint":
            request.mask_name = upload_image(base, args.mask)
        if request.mode == "reference":
            request.reference_name = upload_image(base, args.reference)
        workflow, save_node = build_workflow(request, manifest)
        submitted = http_json("POST", f"{base}/prompt", {"prompt": workflow, "client_id": "ai-image-studio"})
        prompt_id = prompt_id_from_response(submitted)
        eprint(f"queued {prompt_id}")
        image = poll_image(base, prompt_id, save_node, args.job_timeout)
        stamp = datetime.now(timezone.utc)
        job_id = stamp.strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
        output = args.output or Path("outputs") / f"{job_id}.png"
        download_view(base, image, output)
        key = f"outputs/{stamp:%Y/%m/%d}/{job_id}.png"
        sidecar = {
            "job_id": job_id,
            "mode": request.mode,
            "prompt": request.prompt,
            "negative": request.negative,
            "seed": request.seed,
            "steps": request.steps,
            "guidance": request.guidance,
            "denoise": request.denoise,
            "width": request.width,
            "height": request.height,
            "s3_uri": f"s3://{bucket}/{key}",
        }
        meta_path = output.with_suffix(".json")
        meta_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")
        meta_key = f"outputs/{stamp:%Y/%m/%d}/{job_id}.json"
        try:
            run_aws(cfg, ["s3", "cp", str(output), f"s3://{bucket}/{key}", "--sse", "AES256"])
            run_aws(cfg, ["s3", "cp", str(meta_path), f"s3://{bucket}/{meta_key}", "--sse", "AES256"])
        except SystemExit as exc:
            eprint(str(exc))
            eprint(f"local copy kept at {output}")
            exit_code = 1
        else:
            print(f"local: {output}")
            print(f"s3: s3://{bucket}/{key}")
    except RuntimeError as exc:
        eprint(f"error: {exc}")
        exit_code = 1
    finally:
        if tunnel.poll() is None:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=10)
            except subprocess.TimeoutExpired:
                tunnel.kill()
        if args.stop:
            stop_instance(cfg, instance_id)
    return exit_code


def target(args: argparse.Namespace) -> tuple[AwsConfig, str]:
    require_tool("aws")
    cfg = AwsConfig(resolve_region(args.region), args.profile)
    instance_id = args.instance_id or ssm_value(cfg, "/ai-image-studio/instance-id")
    return cfg, instance_id


def cmd_start(args: argparse.Namespace) -> int:
    cfg, instance_id = target(args)
    ensure_running(cfg, instance_id, timeout=900)
    eprint("ComfyUI loads weights after boot. The idle watcher stops the instance when it is quiet.")
    print(instance_id)
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    cfg, instance_id = target(args)
    state = instance_state(cfg, instance_id)
    if state == "stopped":
        print(f"{instance_id} stopped")
        return 0
    stop_instance(cfg, instance_id)
    print(f"{instance_id} stopped")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg, instance_id = target(args)
    result = run_aws(
        cfg,
        [
            "ec2",
            "describe-instances",
            "--instance-ids",
            instance_id,
            "--query",
            "Reservations[0].Instances[0].{State:State.Name,Type:InstanceType,Az:Placement.AvailabilityZone}",
            "--output",
            "json",
        ],
    )
    payload = json.loads(result.stdout)
    ping = run_aws(
        cfg,
        [
            "ssm",
            "describe-instance-information",
            "--filters",
            f"Key=InstanceIds,Values={instance_id}",
            "--query",
            "InstanceInformationList[0].PingStatus",
            "--output",
            "text",
        ],
        check=False,
    )
    payload["InstanceId"] = instance_id
    payload["Ssm"] = ping.stdout.strip() or "unknown"
    print(json.dumps(payload, indent=2))
    return 0


def add_aws_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--region", default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"))
    parser.add_argument("--profile", default=None)
    parser.add_argument("--instance-id", default=None)
    parser.add_argument("--bucket", default=None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Private ComfyUI client for ai-image-studio.")
    sub = parser.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate", help="Run a text, image, or reference job.")
    add_aws_args(generate)
    generate.add_argument("--prompt", required=True)
    generate.add_argument("--negative", default="")
    generate.add_argument("--init", type=Path)
    generate.add_argument("--mask", type=Path)
    generate.add_argument("--reference", type=Path)
    generate.add_argument("--mode", choices=["auto", "txt2img", "img2img", "inpaint", "reference"], default="auto")
    generate.add_argument("--width", type=int, default=1024)
    generate.add_argument("--height", type=int, default=1024)
    generate.add_argument("--steps", type=int)
    generate.add_argument("--guidance", type=float)
    generate.add_argument("--denoise", type=float)
    generate.add_argument("--seed", type=int)
    generate.add_argument("--sampler", default=None)
    generate.add_argument("--scheduler", default=None)
    generate.add_argument("--ip-weight", type=float, default=0.8)
    generate.add_argument("--output", type=Path)
    generate.add_argument("--stop", action="store_true", help="Stop the instance after the job.")
    generate.add_argument("--local-port", type=int, default=0)
    generate.add_argument("--boot-timeout", type=int, default=900)
    generate.add_argument("--ready-timeout", type=int, default=600)
    generate.add_argument("--job-timeout", type=int, default=1200)
    generate.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    generate.add_argument("--dry-run", action="store_true", help="Print the workflow JSON and do not call AWS.")

    for name, help_text in (
        ("start", "Start the GPU instance and wait for SSM."),
        ("stop", "Stop the GPU instance."),
        ("status", "Show whether the instance is running."),
    ):
        command = sub.add_parser(name, help=help_text)
        add_aws_args(command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            return cmd_generate(args)
        if args.command == "start":
            return cmd_start(args)
        if args.command == "stop":
            return cmd_stop(args)
        if args.command == "status":
            return cmd_status(args)
    except ValueError as exc:
        eprint(f"error: {exc}")
        return 2
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
